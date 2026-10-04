import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from time import perf_counter

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from pcg.adapters.anthropic import AnthropicAdapter
from pcg.adapters.types import CanonicalRequest, Usage
from pcg.config import Settings, WireFormat
from pcg.failopen import aguard, analysis_errors, guard
from pcg.records import build_record, tenant_hash_for
from pcg.store import RequestRecord, RequestStore
from pcg.stream_tee import AnthropicStreamSummary, StreamTee, parse_anthropic_stream
from pcg.upstreams import UnknownUpstreamError, resolve_upstream

logger = logging.getLogger(__name__)
failopen_logger = logging.getLogger("pcg.failopen")


def _select_headers(headers: Mapping[str, str], names: tuple[str, ...]) -> dict[str, str]:
    return {name: headers[name] for name in names if name in headers}


def _feed_stream_tee(tee: StreamTee, chunk: bytes) -> bool:
    tee.feed(chunk)
    return True


def _stream_summary(tee: StreamTee) -> AnthropicStreamSummary:
    return parse_anthropic_stream(tee.raw_events_bytes())


def _stream_state(tee: StreamTee) -> tuple[bool, bool]:
    return tee.truncated, tee.failed


async def _insert_record(store: RequestStore, record: RequestRecord) -> None:
    await asyncio.to_thread(store.insert, record)


async def _migrate_store(store: RequestStore) -> bool:
    await asyncio.to_thread(store.migrate)
    return True


def _record_parse_failure(error: Exception) -> None:
    analysis_errors.increment("parse_request")
    try:
        failopen_logger.warning("component=parse_request error=%s", type(error).__name__)
    except Exception:
        return


def create_app(
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    on_stream_complete: Callable[[AnthropicStreamSummary | None, bool, bool], None] | None = None,
    stream_max_buffer_bytes: int = 1_048_576,
    db_url: str | None = None,
) -> FastAPI:
    store: RequestStore | None = None
    if settings.tenant_hash_salt is None:
        logger.warning("request records disabled: PCG_TENANT_HASH_SALT not set")
    else:
        store = guard("store_init", RequestStore, db_url or settings.db_url, default=None)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal store
        async with httpx.AsyncClient(transport=transport, timeout=60.0) as client:
            app.state.http_client = client
            if store is not None:
                migrated = await aguard("store_migration", _migrate_store, store, default=False)
                if not migrated:
                    guard("store_dispose", store.dispose, default=None)
                    store = None
            try:
                yield
            finally:
                if store is not None:
                    guard("store_dispose", store.dispose, default=None)

    app = FastAPI(lifespan=lifespan)
    app.state.analysis_errors = analysis_errors

    async def proxy(
        request: Request,
        path_upstream: str | None,
        background: BackgroundTasks,
    ) -> Response:
        header_upstream = request.headers.get("x-pcg-upstream")
        if path_upstream is not None and header_upstream is not None:
            if path_upstream != header_upstream:
                return JSONResponse({"error": "conflicting upstream selection"}, status_code=400)
        requested = path_upstream if path_upstream is not None else header_upstream

        try:
            upstream_name, upstream = resolve_upstream(
                settings.upstreams,
                requested,
                settings.default_upstream,
            )
        except UnknownUpstreamError:
            return JSONResponse({"error": "unknown upstream"}, status_code=404)

        if upstream.wire_format is not WireFormat.ANTHROPIC_MESSAGES:
            return JSONResponse({"error": "wire format not implemented yet"}, status_code=501)

        raw_body = await request.body()
        adapter = AnthropicAdapter()
        parsed: CanonicalRequest | None
        try:
            parsed = adapter.parse_request(raw_body, request.headers)
        except ValueError:
            return JSONResponse({"error": "invalid request"}, status_code=400)
        except Exception as error:
            _record_parse_failure(error)
            parsed = None

        if parsed is None:
            probe: object = guard("stream_probe", json.loads, raw_body, default=None)
            is_stream = isinstance(probe, dict) and probe.get("stream") is True
        else:
            is_stream = parsed.stream

        default_headers: tuple[str, ...] = (
            "content-type",
            "anthropic-version",
            "x-api-key",
        )
        allowed_headers: tuple[str, ...] = guard(
            "upstream_headers",
            lambda: ("content-type", "anthropic-version", *adapter.auth_header_names()),
            default=default_headers,
        )
        upstream_headers: dict[str, str] = guard(
            "upstream_headers",
            _select_headers,
            request.headers,
            allowed_headers,
            default={},
        )
        upstream_url = f"{upstream.base_url.rstrip('/')}/v1/messages"

        tenant_hash: str | None = None
        salt = settings.tenant_hash_salt
        if store is not None and salt is not None and parsed is not None:
            salt_bytes = guard(
                "tenant_hash",
                lambda: salt.get_secret_value().encode(),
                default=None,
            )
            client_key = guard(
                "tenant_hash",
                request.headers.get,
                "x-api-key",
                "",
                default=None,
            )
            if salt_bytes is not None and client_key is not None:
                tenant_hash = guard(
                    "tenant_hash",
                    tenant_hash_for,
                    salt_bytes,
                    client_key,
                    default=None,
                )
        session_id = guard(
            "session_id",
            request.headers.get,
            "x-pcg-session",
            default=None,
        )
        cache_mode = guard(
            "cache_profile",
            lambda: adapter.cache_profile().cache_mode.value,
            default="unknown",
        )

        def make_record(
            *,
            upstream_status: int | None,
            complete: bool | None,
            truncated: bool,
            usage: Usage | None,
            latency_ms: float,
        ) -> RequestRecord:
            if tenant_hash is None or parsed is None:
                raise RuntimeError("recording is disabled")
            return build_record(
                tenant_hash=tenant_hash,
                session_id=session_id,
                upstream=upstream_name,
                wire_format=upstream.wire_format.value,
                cache_mode=cache_mode,
                model=parsed.model,
                stream=parsed.stream,
                upstream_status=upstream_status,
                complete=complete,
                truncated=truncated,
                usage=usage,
                latency_ms=latency_ms,
                log_mode=settings.log_mode,
            )

        def insert_response_record(
            body: bytes | None,
            upstream_status: int | None,
            latency_ms: float,
        ) -> None:
            active_store = store
            if active_store is None or tenant_hash is None or parsed is None:
                return
            usage: Usage | None = None
            if body is not None and upstream_status is not None and 200 <= upstream_status < 300:
                decoded: object = guard("usage", json.loads, body, default=None)
                if decoded is not None:
                    usage = guard("usage", adapter.extract_usage, decoded, default=None)
            record: RequestRecord | None = guard(
                "record_build",
                make_record,
                upstream_status=upstream_status,
                complete=None,
                truncated=False,
                usage=usage,
                latency_ms=latency_ms,
                default=None,
            )
            if record is not None:
                guard("record_insert", active_store.insert, record, default=None)

        def schedule_record(
            body: bytes | None,
            upstream_status: int | None,
            latency_ms: float,
        ) -> None:
            guard(
                "record_schedule",
                background.add_task,
                insert_response_record,
                body,
                upstream_status,
                latency_ms,
                default=None,
            )

        started_at = perf_counter()
        if is_stream:
            stream_context = app.state.http_client.stream(
                "POST",
                upstream_url,
                headers=upstream_headers,
                content=raw_body,
            )
            try:
                upstream_response = await stream_context.__aenter__()
            except httpx.HTTPError:
                schedule_record(None, None, (perf_counter() - started_at) * 1000)
                return JSONResponse(
                    {"error": "upstream unreachable"}, status_code=502, background=background
                )

            if not upstream_response.is_success:
                try:
                    try:
                        error_body = await upstream_response.aread()
                    except httpx.HTTPError:
                        schedule_record(
                            None,
                            upstream_response.status_code,
                            (perf_counter() - started_at) * 1000,
                        )
                        return JSONResponse(
                            {"error": "upstream unreachable"},
                            status_code=502,
                            background=background,
                        )
                finally:
                    await aguard(
                        "stream_close",
                        stream_context.__aexit__,
                        None,
                        None,
                        None,
                        default=None,
                    )
                schedule_record(
                    error_body,
                    upstream_response.status_code,
                    (perf_counter() - started_at) * 1000,
                )
                response_headers = {}
                if "content-type" in upstream_response.headers:
                    response_headers["content-type"] = upstream_response.headers["content-type"]
                return Response(
                    content=error_body,
                    status_code=upstream_response.status_code,
                    headers=response_headers,
                    background=background,
                )

            tee: StreamTee | None = guard(
                "stream_tee",
                StreamTee,
                stream_max_buffer_bytes,
                default=None,
            )

            async def stream_body() -> AsyncIterator[bytes]:
                summary: AnthropicStreamSummary | None = None
                feed_failed = tee is None
                try:
                    async for chunk in upstream_response.aiter_raw():
                        if tee is not None:
                            feed_ok = guard(
                                "stream_tee",
                                _feed_stream_tee,
                                tee,
                                chunk,
                                default=False,
                            )
                            feed_failed = feed_failed or not feed_ok
                        yield chunk
                finally:
                    await aguard(
                        "stream_close",
                        stream_context.__aexit__,
                        None,
                        None,
                        None,
                        default=None,
                    )
                    if tee is not None:
                        summary = guard(
                            "stream_summary",
                            _stream_summary,
                            tee,
                            default=None,
                        )
                        truncated, tee_failed = guard(
                            "stream_tee",
                            _stream_state,
                            tee,
                            default=(False, True),
                        )
                    else:
                        truncated, tee_failed = False, True
                    guard(
                        "stream_summary",
                        setattr,
                        request.state,
                        "stream_summary",
                        summary,
                        default=None,
                    )
                    if on_stream_complete is not None:
                        guard(
                            "callback",
                            on_stream_complete,
                            summary,
                            truncated,
                            tee_failed or feed_failed,
                            default=None,
                        )
                    active_store = store
                    if active_store is not None and tenant_hash is not None and parsed is not None:
                        record: RequestRecord | None = guard(
                            "record_build",
                            make_record,
                            upstream_status=upstream_response.status_code,
                            complete=summary.complete if summary is not None else False,
                            truncated=truncated,
                            usage=summary.usage if summary is not None else None,
                            latency_ms=(perf_counter() - started_at) * 1000,
                            default=None,
                        )
                        if record is not None:
                            await aguard(
                                "record_insert",
                                _insert_record,
                                active_store,
                                record,
                                default=None,
                            )
                    guard(
                        "logging",
                        logger.info,
                        "upstream=%s status=%d complete=%s truncated=%s duration_ms=%.3f",
                        upstream_name,
                        upstream_response.status_code,
                        summary.complete if summary is not None else False,
                        truncated,
                        (perf_counter() - started_at) * 1000,
                        default=None,
                    )

            content_type = upstream_response.headers.get("content-type", "text/event-stream")
            return StreamingResponse(
                stream_body(),
                status_code=upstream_response.status_code,
                headers={"content-type": content_type},
                background=background,
            )

        try:
            upstream_response = await app.state.http_client.post(
                upstream_url,
                headers=upstream_headers,
                content=raw_body,
            )
        except httpx.HTTPError:
            schedule_record(None, None, (perf_counter() - started_at) * 1000)
            return JSONResponse(
                {"error": "upstream unreachable"}, status_code=502, background=background
            )

        schedule_record(
            upstream_response.content,
            upstream_response.status_code,
            (perf_counter() - started_at) * 1000,
        )
        response_headers = {}
        if "content-type" in upstream_response.headers:
            response_headers["content-type"] = upstream_response.headers["content-type"]
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            headers=response_headers,
            background=background,
        )

    @app.post("/v1/messages")
    async def default_messages(request: Request, background: BackgroundTasks) -> Response:
        return await proxy(request, None, background)

    @app.post("/u/{upstream}/v1/messages")
    async def named_messages(
        upstream: str, request: Request, background: BackgroundTasks
    ) -> Response:
        return await proxy(request, upstream, background)

    return app
