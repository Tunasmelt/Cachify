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
from pcg.adapters.types import Usage
from pcg.config import Settings, WireFormat
from pcg.records import build_record, tenant_hash_for
from pcg.store import RequestRecord, RequestStore
from pcg.stream_tee import AnthropicStreamSummary, StreamTee, parse_anthropic_stream
from pcg.upstreams import UnknownUpstreamError, resolve_upstream

logger = logging.getLogger(__name__)


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
        try:
            store = RequestStore(db_url or settings.db_url)
        except Exception as error:
            logger.error("request store initialization failed error=%s", type(error).__name__)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal store
        async with httpx.AsyncClient(transport=transport, timeout=60.0) as client:
            app.state.http_client = client
            if store is not None:
                try:
                    await asyncio.to_thread(store.migrate)
                except Exception as error:
                    logger.error("request store migration failed error=%s", type(error).__name__)
                    store.dispose()
                    store = None
            try:
                yield
            finally:
                if store is not None:
                    store.dispose()

    app = FastAPI(lifespan=lifespan)

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
        try:
            parsed = adapter.parse_request(raw_body, request.headers)
        except ValueError:
            return JSONResponse({"error": "invalid request"}, status_code=400)
        allowed_headers = ("content-type", "anthropic-version", *adapter.auth_header_names())
        upstream_headers: Mapping[str, str] = {
            name: request.headers[name] for name in allowed_headers if name in request.headers
        }
        upstream_url = f"{upstream.base_url.rstrip('/')}/v1/messages"
        tenant_hash = (
            tenant_hash_for(
                settings.tenant_hash_salt.get_secret_value().encode(),
                request.headers.get("x-api-key", ""),
            )
            if store is not None and settings.tenant_hash_salt is not None
            else None
        )
        session_id = request.headers.get("x-pcg-session")
        cache_mode = adapter.cache_profile().cache_mode.value

        def make_record(
            *,
            upstream_status: int | None,
            complete: bool | None,
            truncated: bool,
            usage: Usage | None,
            latency_ms: float,
        ) -> RequestRecord:
            if tenant_hash is None:
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
            if active_store is None or tenant_hash is None:
                return
            usage = None
            if body is not None and upstream_status is not None and 200 <= upstream_status < 300:
                try:
                    usage = adapter.extract_usage(json.loads(body))
                except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
                    pass
            record = make_record(
                upstream_status=upstream_status,
                complete=None,
                truncated=False,
                usage=usage,
                latency_ms=latency_ms,
            )
            try:
                active_store.insert(record)
            except Exception as error:
                logger.error(
                    "request record insert failed upstream=%s status=%s error=%s",
                    upstream_name,
                    upstream_status,
                    type(error).__name__,
                )

        started_at = perf_counter()
        if parsed.stream:
            stream_context = app.state.http_client.stream(
                "POST",
                upstream_url,
                headers=upstream_headers,
                content=raw_body,
            )
            try:
                upstream_response = await stream_context.__aenter__()
            except httpx.HTTPError:
                background.add_task(
                    insert_response_record,
                    None,
                    None,
                    (perf_counter() - started_at) * 1000,
                )
                return JSONResponse(
                    {"error": "upstream unreachable"}, status_code=502, background=background
                )

            if not upstream_response.is_success:
                try:
                    try:
                        error_body = await upstream_response.aread()
                    except httpx.HTTPError:
                        background.add_task(
                            insert_response_record,
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
                    await stream_context.__aexit__(None, None, None)
                background.add_task(
                    insert_response_record,
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

            tee = StreamTee(max_buffer_bytes=stream_max_buffer_bytes)

            async def stream_body() -> AsyncIterator[bytes]:
                summary: AnthropicStreamSummary | None = None
                feed_failed = False
                try:
                    async for chunk in upstream_response.aiter_raw():
                        try:
                            tee.feed(chunk)
                        except Exception:
                            feed_failed = True
                        yield chunk
                finally:
                    await stream_context.__aexit__(None, None, None)
                    try:
                        summary = parse_anthropic_stream(tee.raw_events_bytes())
                    except Exception:
                        summary = None
                    request.state.stream_summary = summary
                    if on_stream_complete is not None:
                        try:
                            on_stream_complete(
                                summary,
                                tee.truncated,
                                tee.failed or feed_failed,
                            )
                        except Exception:
                            logger.debug("stream completion callback failed")
                    active_store = store
                    if active_store is not None and tenant_hash is not None:
                        record = make_record(
                            upstream_status=upstream_response.status_code,
                            complete=summary.complete if summary is not None else False,
                            truncated=tee.truncated,
                            usage=summary.usage if summary is not None else None,
                            latency_ms=(perf_counter() - started_at) * 1000,
                        )
                        try:
                            await asyncio.to_thread(active_store.insert, record)
                        except Exception as error:
                            logger.error(
                                "request record insert failed upstream=%s status=%s error=%s",
                                upstream_name,
                                upstream_response.status_code,
                                type(error).__name__,
                            )
                    logger.info(
                        "upstream=%s status=%d complete=%s truncated=%s duration_ms=%.3f",
                        upstream_name,
                        upstream_response.status_code,
                        summary.complete if summary is not None else False,
                        tee.truncated,
                        (perf_counter() - started_at) * 1000,
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
            background.add_task(
                insert_response_record,
                None,
                None,
                (perf_counter() - started_at) * 1000,
            )
            return JSONResponse(
                {"error": "upstream unreachable"}, status_code=502, background=background
            )

        background.add_task(
            insert_response_record,
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
