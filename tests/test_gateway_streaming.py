import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pcg.config import Settings
from pcg.gateway import create_app
from pcg.stream_tee import AnthropicStreamSummary, StreamTee

pytestmark = pytest.mark.proxy
BASE_URL = "https://provider.example.invalid"
FIXTURES = Path(__file__).parent / "fixtures" / "anthropic"


class ChunkedAsyncStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes, chunk_size: int = 7) -> None:
        self.content = content
        self.chunk_size = chunk_size
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for start in range(0, len(self.content), self.chunk_size):
            yield self.content[start : start + self.chunk_size]

    async def aclose(self) -> None:
        self.closed = True


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        upstreams={
            "primary": {
                "wire_format": "anthropic-messages",
                "base_url": BASE_URL,
                "auth_style": "x-api-key",
            }
        },
        default_upstream="primary",
    )


def request_body() -> bytes:
    return (
        b'{"model":"claude-sonnet-4-5-test","max_tokens":32,"stream":true,'
        b'"messages":[{"role":"user","content":"Hello"}]}'
    )


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def stream_handler(
    content: bytes,
    streams: list[ChunkedAsyncStream] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        stream = ChunkedAsyncStream(content)
        if streams is not None:
            streams.append(stream)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=stream,
        )

    return handler


def test_stream_is_byte_identical_and_callback_gets_summary() -> None:
    content = fixture_bytes("stream_plain.sse")
    completed: list[tuple[AnthropicStreamSummary | None, bool, bool]] = []
    app = create_app(
        make_settings(),
        transport=httpx.MockTransport(stream_handler(content)),
        on_stream_complete=lambda summary, truncated, failed: completed.append(
            (summary, truncated, failed)
        ),
    )

    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body())

    assert response.content == content
    assert response.headers["content-type"] == "text/event-stream"
    assert len(completed) == 1
    summary, truncated, failed = completed[0]
    assert summary is not None
    assert summary.complete is True
    assert summary.stop_reason == "end_turn"
    assert summary.usage is not None
    assert summary.usage.cache_write_tokens == 11
    assert summary.usage.cache_read_tokens == 23
    assert truncated is False
    assert failed is False


def test_incomplete_stream_reaches_client_and_callback() -> None:
    content = fixture_bytes("stream_truncated.sse")
    completed: list[tuple[AnthropicStreamSummary | None, bool, bool]] = []
    app = create_app(
        make_settings(),
        transport=httpx.MockTransport(stream_handler(content)),
        on_stream_complete=lambda summary, truncated, failed: completed.append(
            (summary, truncated, failed)
        ),
    )

    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body())

    assert response.content == content
    assert completed[0][0] is not None
    assert completed[0][0].complete is False


def test_tiny_analysis_buffer_does_not_truncate_client_stream() -> None:
    content = fixture_bytes("stream_plain.sse")
    completed: list[tuple[AnthropicStreamSummary | None, bool, bool]] = []
    app = create_app(
        make_settings(),
        transport=httpx.MockTransport(stream_handler(content)),
        on_stream_complete=lambda summary, truncated, failed: completed.append(
            (summary, truncated, failed)
        ),
        stream_max_buffer_bytes=8,
    )

    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body())

    assert response.content == content
    assert completed[0][1] is True
    assert completed[0][2] is False


def test_streaming_request_passes_through_upstream_error_without_callback() -> None:
    error_body = b'{"type":"error","error":{"message":"synthetic rate limit"}}'
    completed: list[tuple[AnthropicStreamSummary | None, bool, bool]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            429,
            content=error_body,
            headers={"content-type": "application/json"},
        )

    app = create_app(
        make_settings(),
        transport=httpx.MockTransport(handler),
        on_stream_complete=lambda summary, truncated, failed: completed.append(
            (summary, truncated, failed)
        ),
    )
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body())

    assert response.status_code == 429
    assert response.content == error_body
    assert response.headers["content-type"] == "application/json"
    assert completed == []


def test_streaming_transport_error_returns_bad_gateway() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic connection failure", request=request)

    app = create_app(make_settings(), transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body())

    assert response.status_code == 502
    assert response.json() == {"error": "upstream unreachable"}


def test_tee_exception_does_not_interrupt_client_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    content = fixture_bytes("stream_plain.sse")

    def raise_tee_error(self: StreamTee, chunk: bytes) -> None:
        del self, chunk
        raise RuntimeError("synthetic analysis failure")

    monkeypatch.setattr(StreamTee, "feed", raise_tee_error)
    app = create_app(make_settings(), transport=httpx.MockTransport(stream_handler(content)))

    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body())

    assert response.content == content


def test_closing_client_stream_closes_upstream_stream() -> None:
    content = fixture_bytes("stream_plain.sse")
    streams: list[ChunkedAsyncStream] = []
    app = create_app(
        make_settings(),
        transport=httpx.MockTransport(stream_handler(content, streams)),
    )

    with TestClient(app) as client:
        with client.stream("POST", "/v1/messages", content=request_body()) as response:
            next(response.iter_bytes())

    assert len(streams) == 1
    assert streams[0].closed is True


def test_streaming_api_key_is_absent_from_logs(caplog: pytest.LogCaptureFixture) -> None:
    sentinel = "test-client-key-sentinel"
    content = fixture_bytes("stream_plain.sse")
    app = create_app(make_settings(), transport=httpx.MockTransport(stream_handler(content)))

    caplog.set_level(logging.DEBUG)
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages",
            content=request_body(),
            headers={"x-api-key": sentinel},
        )

    assert response.content == content
    assert all(sentinel not in record.getMessage() for record in caplog.records)
