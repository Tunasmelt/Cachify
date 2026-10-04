import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pcg.adapters.anthropic import AnthropicAdapter
from pcg.config import Settings
from pcg.failopen import aguard, analysis_errors, guard
from pcg.gateway import create_app
from pcg.store import RequestStore
from tests.test_gateway_streaming import fixture_bytes, stream_handler

pytestmark = pytest.mark.proxy
BASE_URL = "https://provider.example.invalid"
SALT = "x" * 32


@pytest.fixture(autouse=True)
def reset_analysis_errors() -> None:
    analysis_errors.reset()


def make_settings(*, salt: str | None = None) -> Settings:
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
        tenant_hash_salt=salt,
    )


def request_body(*, stream: bool = False) -> bytes:
    stream_value = b"true" if stream else b"false"
    return (
        b'{"model":"claude-sonnet-4-5-test","max_tokens":32,"stream":'
        + stream_value
        + b',"messages":[{"role":"user","content":"Hello"}]}'
    )


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def plain_handler(
    body: bytes,
    seen: list[httpx.Request] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/json"},
        )

    return handler


def test_guard_returns_default_counts_failure_and_hides_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "private-exception-sentinel"

    def fail() -> str:
        raise RuntimeError(f"boom {sentinel}")

    caplog.set_level(logging.WARNING, logger="pcg.failopen")
    assert guard("record_insert", fail, default="fallback") == "fallback"
    assert analysis_errors.snapshot() == {"record_insert": 1}
    assert "component=record_insert error=RuntimeError" in caplog.text
    assert sentinel not in caplog.text


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_guard_does_not_catch_base_exceptions(error_type: type[BaseException]) -> None:
    def stop() -> None:
        raise error_type

    with pytest.raises(error_type):
        guard("callback", stop, default=None)

    assert analysis_errors.snapshot() == {}


async def test_aguard_returns_default_and_does_not_catch_cancelled_error() -> None:
    async def fail() -> str:
        raise RuntimeError("synthetic async failure")

    async def cancel() -> str:
        raise asyncio.CancelledError

    assert await aguard("record_insert", fail, default="fallback") == "fallback"
    assert analysis_errors.snapshot() == {"record_insert": 1}
    with pytest.raises(asyncio.CancelledError):
        await aguard("callback", cancel, default="fallback")


def test_cache_profile_failure_still_returns_exact_upstream_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream_body = b'{"type":"message","content":[]}'
    seen: list[httpx.Request] = []

    def fail_cache_profile(self: AnthropicAdapter) -> None:
        del self
        raise RuntimeError("synthetic cache profile failure")

    monkeypatch.setattr(AnthropicAdapter, "cache_profile", fail_cache_profile)
    app = create_app(
        make_settings(), transport=httpx.MockTransport(plain_handler(upstream_body, seen))
    )
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body())

    assert (response.status_code, response.content) == (200, upstream_body)
    assert len(seen) == 1
    assert analysis_errors.snapshot()["cache_profile"] == 1


def test_tenant_hash_failure_still_returns_exact_upstream_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream_body = b'{"type":"message","content":[]}'

    def fail_tenant_hash(salt: bytes, key: str) -> str:
        del salt, key
        raise RuntimeError("synthetic tenant hash failure")

    monkeypatch.setattr("pcg.gateway.tenant_hash_for", fail_tenant_hash)
    app = create_app(
        make_settings(salt=SALT),
        transport=httpx.MockTransport(plain_handler(upstream_body)),
        db_url=sqlite_url(tmp_path / "tenant.db"),
    )
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body())

    assert (response.status_code, response.content) == (200, upstream_body)
    assert analysis_errors.snapshot()["tenant_hash"] == 1


def test_unexpected_parse_failure_forwards_original_non_stream_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream_body = b'{"type":"message","content":[]}'
    sent_body = request_body()
    seen: list[httpx.Request] = []

    def fail_parse(self: AnthropicAdapter, raw_body: bytes, headers: object) -> None:
        del self, raw_body, headers
        raise RuntimeError("synthetic parse failure")

    monkeypatch.setattr(AnthropicAdapter, "parse_request", fail_parse)
    app = create_app(
        make_settings(), transport=httpx.MockTransport(plain_handler(upstream_body, seen))
    )
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=sent_body)

    assert (response.status_code, response.content) == (200, upstream_body)
    assert len(seen) == 1
    assert seen[0].content == sent_body
    assert analysis_errors.snapshot() == {"parse_request": 1}


def test_unexpected_parse_failure_uses_stream_probe_and_preserves_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = fixture_bytes("stream_plain.sse")

    def fail_parse(self: AnthropicAdapter, raw_body: bytes, headers: object) -> None:
        del self, raw_body, headers
        raise RuntimeError("synthetic parse failure")

    monkeypatch.setattr(AnthropicAdapter, "parse_request", fail_parse)
    app = create_app(make_settings(), transport=httpx.MockTransport(stream_handler(content)))
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body(stream=True))

    assert response.content == content
    assert analysis_errors.snapshot() == {"parse_request": 1}


def test_parse_value_error_remains_client_error_without_analysis_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def reject_parse(self: AnthropicAdapter, raw_body: bytes, headers: object) -> None:
        del self, raw_body, headers
        raise ValueError("synthetic invalid request")

    monkeypatch.setattr(AnthropicAdapter, "parse_request", reject_parse)
    app = create_app(make_settings(), transport=httpx.MockTransport(plain_handler(b"{}", seen)))
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body())

    assert (response.status_code, response.json()) == (400, {"error": "invalid request"})
    assert seen == []
    assert analysis_errors.snapshot() == {}


def test_stream_summary_failure_preserves_client_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = fixture_bytes("stream_plain.sse")

    def fail_summary(data: bytes) -> None:
        del data
        raise RuntimeError("synthetic stream summary failure")

    monkeypatch.setattr("pcg.gateway.parse_anthropic_stream", fail_summary)
    app = create_app(make_settings(), transport=httpx.MockTransport(stream_handler(content)))
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body(stream=True))

    assert response.content == content
    assert analysis_errors.snapshot()["stream_summary"] == 1


def test_stream_callback_failure_preserves_client_bytes() -> None:
    content = fixture_bytes("stream_plain.sse")

    def fail_callback(summary: object, truncated: bool, failed: bool) -> None:
        del summary, truncated, failed
        raise RuntimeError("synthetic callback failure")

    app = create_app(
        make_settings(),
        transport=httpx.MockTransport(stream_handler(content)),
        on_stream_complete=fail_callback,
    )
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body(stream=True))

    assert response.content == content
    assert analysis_errors.snapshot()["callback"] == 1


def test_stream_record_insert_failure_preserves_client_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = fixture_bytes("stream_plain.sse")

    def fail_insert(self: RequestStore, record: object) -> None:
        del self, record
        raise RuntimeError("synthetic insert failure")

    monkeypatch.setattr(RequestStore, "insert", fail_insert)
    app = create_app(
        make_settings(salt=SALT),
        transport=httpx.MockTransport(stream_handler(content)),
        db_url=sqlite_url(tmp_path / "stream.db"),
    )
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body(stream=True))

    assert response.content == content
    assert analysis_errors.snapshot()["record_insert"] == 1


def test_analysis_failure_preserves_status_and_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream_body = b'{"type":"message","content":[]}'
    transport = httpx.MockTransport(plain_handler(upstream_body))
    baseline_app = create_app(make_settings(), transport=transport)
    with TestClient(baseline_app) as client:
        baseline = client.post("/v1/messages", content=request_body())

    analysis_errors.reset()

    def fail_cache_profile(self: AnthropicAdapter) -> None:
        del self
        raise RuntimeError("synthetic cache profile failure")

    monkeypatch.setattr(AnthropicAdapter, "cache_profile", fail_cache_profile)
    failing_app = create_app(
        make_settings(), transport=httpx.MockTransport(plain_handler(upstream_body))
    )
    with TestClient(failing_app) as client:
        response = client.post("/v1/messages", content=request_body())

    assert response.status_code == baseline.status_code
    assert response.headers["content-type"] == baseline.headers["content-type"]


def test_app_exposes_exact_counter_snapshot() -> None:
    app = create_app(make_settings(), transport=httpx.MockTransport(plain_handler(b"{}")))

    def fail() -> None:
        raise RuntimeError("synthetic failure")

    guard("usage", fail, default=None)
    guard("usage", fail, default=None)
    guard("callback", fail, default=None)

    assert app.state.analysis_errors.snapshot() == {"usage": 2, "callback": 1}


def test_exception_message_never_leaks_key_to_logs_or_response(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "test-client-key-sentinel"
    upstream_body = b'{"type":"message","content":[]}'

    def fail_cache_profile(self: AnthropicAdapter) -> None:
        del self
        raise RuntimeError(f"boom {sentinel}")

    monkeypatch.setattr(AnthropicAdapter, "cache_profile", fail_cache_profile)
    caplog.set_level(logging.DEBUG)
    app = create_app(make_settings(), transport=httpx.MockTransport(plain_handler(upstream_body)))
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages",
            content=request_body(),
            headers={"x-api-key": sentinel},
        )

    assert sentinel not in response.text
    assert all(sentinel not in record.getMessage() for record in caplog.records)
