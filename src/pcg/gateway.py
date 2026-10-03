from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from pcg.adapters.anthropic import AnthropicAdapter
from pcg.config import Settings, WireFormat
from pcg.upstreams import UnknownUpstreamError, resolve_upstream


def create_app(
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
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
            _, upstream = resolve_upstream(
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
        if parsed.stream:
            return JSONResponse({"error": "streaming arrives in M1.2"}, status_code=501)

        allowed_headers = ("content-type", "anthropic-version", *adapter.auth_header_names())
        upstream_headers: Mapping[str, str] = {
            name: request.headers[name] for name in allowed_headers if name in request.headers
        }
        try:
            upstream_response = await app.state.http_client.post(
                f"{upstream.base_url.rstrip('/')}/v1/messages",
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
