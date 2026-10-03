import logging
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from pcg.config import Settings
from pcg.gateway import create_app

pytestmark = pytest.mark.proxy
BASE_URL = "https://provider.example.invalid"
CLIENT_KEY = "test-client-key"


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        upstreams={
            "primary": {
                "wire_format": "anthropic-messages",
                "base_url": BASE_URL,
                "auth_style": "x-api-key",
            },
            "chat": {
                "wire_format": "openai-chat",
                "base_url": BASE_URL,
                "auth_style": "bearer",
            },
        },
        default_upstream="primary",
    )


def request_body(*, stream: bool = False) -> bytes:
    stream_value = b"true" if stream else b"false"
    return (
        b'{"model":"claude-sonnet-4-5-test","max_tokens":32,"stream":'
        + stream_value
        + b',"messages":[{"role":"user","content":"Hello"}]}'
    )


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> TestClient:
    return TestClient(create_app(make_settings(), transport=httpx.MockTransport(handler)))


def test_plain_response_and_request_bodies_are_unchanged() -> None:
    seen: list[httpx.Request] = []
    upstream_body = b'{"type":"message","stop_reason":"end_turn","content":[]}'

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=upstream_body,
            headers={"content-type": "application/json"},
        )

    body = request_body()
    with client_for(handler) as client:
        response = client.post(
            "/v1/messages",
            content=body,
            headers={"content-type": "application/json", "x-api-key": CLIENT_KEY},
        )

    assert response.status_code == 200
    assert response.content == upstream_body
    assert len(seen) == 1
    assert seen[0].content == body
    assert seen[0].url == f"{BASE_URL}/v1/messages"


@pytest.mark.parametrize("status_code", [429, 500])
def test_upstream_errors_are_passed_through(status_code: int) -> None:
    upstream_body = b'{"type":"error","error":{"message":"synthetic failure"}}'

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            status_code,
            content=upstream_body,
            headers={"content-type": "application/json"},
        )

    with client_for(handler) as client:
        response = client.post("/v1/messages", content=request_body())

    assert response.status_code == status_code
    assert response.content == upstream_body


def test_only_allowed_client_auth_header_is_forwarded() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})

    with client_for(handler) as client:
        client.post(
            "/v1/messages",
            content=request_body(),
            headers={
                "x-api-key": CLIENT_KEY,
                "authorization": "Bearer synthetic-other-auth",
                "x-goog-api-key": "synthetic-google-key",
                "anthropic-version": "2023-06-01",
            },
        )

    assert seen[0].headers["x-api-key"] == CLIENT_KEY
    assert "authorization" not in seen[0].headers
    assert "x-goog-api-key" not in seen[0].headers
    assert seen[0].headers["anthropic-version"] == "2023-06-01"


def test_streaming_request_is_not_forwarded() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        return httpx.Response(200)

    with client_for(handler) as client:
        response = client.post("/v1/messages", content=request_body(stream=True))

    assert response.status_code == 501
    assert response.json() == {"error": "streaming arrives in M1.2"}
    assert calls == 0


@pytest.mark.parametrize(
    ("path", "headers"),
    [
        ("/u/missing/v1/messages", {}),
        ("/v1/messages", {"x-pcg-upstream": "missing"}),
    ],
)
def test_unknown_upstream_is_rejected(path: str, headers: dict[str, str]) -> None:
    with client_for(lambda request: httpx.Response(200)) as client:
        response = client.post(path, content=request_body(), headers=headers)

    assert response.status_code == 404
    assert response.json() == {"error": "unknown upstream"}


def test_conflicting_upstream_selection_is_rejected() -> None:
    with client_for(lambda request: httpx.Response(200)) as client:
        response = client.post(
            "/u/primary/v1/messages",
            content=request_body(),
            headers={"x-pcg-upstream": "chat"},
        )

    assert response.status_code == 400


def test_unimplemented_wire_format_is_rejected() -> None:
    with client_for(lambda request: httpx.Response(200)) as client:
        response = client.post("/u/chat/v1/messages", content=request_body())

    assert response.status_code == 501
    assert response.json() == {"error": "wire format not implemented yet"}


def test_transport_error_returns_bad_gateway() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic connection failure", request=request)

    with client_for(handler) as client:
        response = client.post("/v1/messages", content=request_body())

    assert response.status_code == 502
    assert response.json() == {"error": "upstream unreachable"}


def test_key_is_absent_from_logs_response_and_settings_repr(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "test-client-key-sentinel"
    settings = make_settings()

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, content=b'{"error":"synthetic upstream failure"}')

    caplog.set_level(logging.DEBUG)
    with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
        response = client.post(
            "/v1/messages",
            content=request_body(),
            headers={"x-api-key": sentinel},
        )

    assert sentinel not in response.text
    assert sentinel not in repr(settings)
    assert all(sentinel not in record.getMessage() for record in caplog.records)
