import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from time import perf_counter

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from pcg.adapters.anthropic import AnthropicAdapter
from pcg.config import Settings, WireFormat
from pcg.stream_tee import AnthropicStreamSummary, StreamTee, parse_anthropic_stream
from pcg.upstreams import UnknownUpstreamError, resolve_upstream

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    on_stream_complete: Callable[[AnthropicStreamSummary | None, bool, bool], None] | None = None,
    stream_max_buffer_bytes: int = 1_048_576,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(transport=transport, timeout=60.0) as client:
            app.state.http_client = client
            yield

    app = FastAPI(lifespan=lifespan)

    async def proxy(request: Request, path_upstream: str | None) -> Response:
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
        if parsed.stream:
            started_at = perf_counter()
            stream_context = app.state.http_client.stream(
                "POST",
                upstream_url,
                headers=upstream_headers,
                content=raw_body,
            )
            try:
                upstream_response = await stream_context.__aenter__()
            except httpx.HTTPError:
                return JSONResponse({"error": "upstream unreachable"}, status_code=502)

            if not upstream_response.is_success:
                try:
                    try:
                        error_body = await upstream_response.aread()
                    except httpx.HTTPError:
                        return JSONResponse({"error": "upstream unreachable"}, status_code=502)
                finally:
                    await stream_context.__aexit__(None, None, None)
                response_headers = {}
                if "content-type" in upstream_response.headers:
                    response_headers["content-type"] = upstream_response.headers["content-type"]
                return Response(
                    content=error_body,
                    status_code=upstream_response.status_code,
                    headers=response_headers,
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
            )

        try:
            upstream_response = await app.state.http_client.post(
                upstream_url,
                headers=upstream_headers,
                content=raw_body,
            )
        except httpx.HTTPError:
            return JSONResponse({"error": "upstream unreachable"}, status_code=502)

        response_headers = {}
        if "content-type" in upstream_response.headers:
            response_headers["content-type"] = upstream_response.headers["content-type"]
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            headers=response_headers,
        )

    @app.post("/v1/messages")
    async def default_messages(request: Request) -> Response:
        return await proxy(request, None)

    @app.post("/u/{upstream}/v1/messages")
    async def named_messages(upstream: str, request: Request) -> Response:
        return await proxy(request, upstream)

    return app
