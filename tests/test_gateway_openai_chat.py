import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from pcg.config import Settings
from pcg.gateway import create_app
from pcg.records import tenant_hash_for
from pcg.store import requests
from tests.test_gateway_streaming import ChunkedAsyncStream

pytestmark = pytest.mark.proxy
FIXTURES = Path(__file__).parent / "fixtures" / "openai_chat"
OAI_URL = "https://oai.example.invalid"
ANTHROPIC_URL = "https://anthropic.example.invalid"
SALT = "x" * 32
KEY = "test-client-key-sentinel"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def make_settings(*, records: bool = False) -> Settings:
    return Settings(
        _env_file=None,
        upstreams={
            "oai": {"wire_format": "openai-chat", "base_url": OAI_URL, "auth_style": "bearer"},
            "anth": {
                "wire_format": "anthropic-messages",
                "base_url": ANTHROPIC_URL,
                "auth_style": "x-api-key",
            },
        },
        default_upstream="oai",
        tenant_hash_salt=SALT if records else None,
    )


def request_body(*, stream: bool = False) -> bytes:
    flag = b"true" if stream else b"false"
    return (
        b'{"model":"gpt-test","stream":'
        + flag
        + b',"messages":[{"role":"system","content":"S"},{"role":"user","content":"Hello"}]}'
    )


def client_for(handler: object, db_path: Path | None = None) -> TestClient:
    app = create_app(
        make_settings(records=db_path is not None),
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        db_url=f"sqlite:///{db_path.as_posix()}" if db_path else None,
    )
    return TestClient(app)


def rows_at(path: Path) -> list[dict[str, object]]:
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        with engine.connect() as connection:
            query = select(requests).order_by(requests.c.created_at)
            return [dict(row) for row in connection.execute(query).mappings()]
    finally:
        engine.dispose()


AUTH = {"authorization": f"Bearer {KEY}"}


def test_non_stream_response_is_byte_identical_and_hits_chat_path() -> None:
    body = fixture_bytes("response_plain.json")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    with client_for(handler) as client:
        response = client.post("/v1/chat/completions", content=request_body(), headers=AUTH)

    assert response.content == body
    assert str(seen[0].url) == f"{OAI_URL}/v1/chat/completions"
    assert seen[0].content == request_body()


def test_named_upstream_route_works() -> None:
    with client_for(lambda request: httpx.Response(200, content=b"{}")) as client:
        response = client.post("/u/oai/v1/chat/completions", content=request_body(), headers=AUTH)

    assert response.status_code == 200


def test_only_openai_headers_are_forwarded() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"{}")

    with client_for(handler) as client:
        client.post(
            "/v1/chat/completions",
            content=request_body(),
            headers={
                **AUTH,
                "openai-organization": "org-1",
                "x-api-key": "should-not-forward",
                "cookie": "session=1",
                "x-pcg-session": "s",
            },
        )

    forwarded = seen[0].headers
    assert forwarded["authorization"] == f"Bearer {KEY}"
    assert forwarded["openai-organization"] == "org-1"
    for name in ("x-api-key", "cookie", "x-pcg-session", "anthropic-version"):
        assert name not in forwarded


@pytest.mark.parametrize("status_code", [400, 429, 500])
def test_upstream_errors_are_passed_through_unchanged(status_code: int) -> None:
    body = fixture_bytes("response_error.json")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            status_code, content=body, headers={"content-type": "application/json"}
        )

    with client_for(handler) as client:
        response = client.post("/v1/chat/completions", content=request_body(), headers=AUTH)

    assert (response.status_code, response.content) == (status_code, body)


def test_stream_is_byte_identical() -> None:
    content = fixture_bytes("stream_plain.sse")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=ChunkedAsyncStream(content)
        )

    with client_for(handler) as client:
        response = client.post(
            "/v1/chat/completions", content=request_body(stream=True), headers=AUTH
        )

    assert response.content == content
    assert response.headers["content-type"].startswith("text/event-stream")


def test_endpoint_and_upstream_wire_format_must_match() -> None:
    with client_for(lambda request: httpx.Response(200)) as client:
        chat_on_anthropic = client.post(
            "/u/anth/v1/chat/completions", content=request_body(), headers=AUTH
        )
        messages_on_openai = client.post("/u/oai/v1/messages", content=request_body())

    expected = {"error": "upstream wire format does not match endpoint"}
    assert (chat_on_anthropic.status_code, chat_on_anthropic.json()) == (400, expected)
    assert (messages_on_openai.status_code, messages_on_openai.json()) == (400, expected)


def test_unknown_upstream_is_rejected() -> None:
    with client_for(lambda request: httpx.Response(200)) as client:
        response = client.post("/u/missing/v1/chat/completions", content=request_body())

    assert (response.status_code, response.json()) == (404, {"error": "unknown upstream"})


def test_invalid_body_is_a_400() -> None:
    with client_for(lambda request: httpx.Response(200)) as client:
        response = client.post("/v1/chat/completions", content=b'{"messages":[]}', headers=AUTH)

    assert (response.status_code, response.json()) == (400, {"error": "invalid request"})


def test_record_uses_normalized_usage_and_bearer_token_tenant(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db_path = tmp_path / "pcg.db"
    body = fixture_bytes("response_plain.json")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    caplog.set_level(logging.DEBUG)
    with client_for(handler, db_path) as client:
        client.post("/v1/chat/completions", content=request_body(), headers=AUTH)

    (row,) = rows_at(db_path)
    assert row["wire_format"] == "openai-chat"
    assert row["cache_mode"] == "auto_prefix"
    assert row["upstream"] == "oai"
    assert row["input_tokens"] == 86  # 2006 prompt tokens minus 1920 cached (ADR-016)
    assert row["output_tokens"] == 300
    assert row["cache_read_tokens"] == 1920
    assert row["cache_write_tokens"] is None
    # Tenant hash is of the bare token, not the "Bearer " header value.
    assert row["tenant_hash"] == tenant_hash_for(SALT.encode(), KEY)
    assert KEY.encode() not in db_path.read_bytes()
    assert all(KEY not in record.getMessage() for record in caplog.records)


def test_streaming_record_has_usage_when_client_asked_for_it(tmp_path: Path) -> None:
    db_path = tmp_path / "pcg.db"
    content = fixture_bytes("stream_plain.sse")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=ChunkedAsyncStream(content)
        )

    with client_for(handler, db_path) as client:
        client.post("/v1/chat/completions", content=request_body(stream=True), headers=AUTH)

    (row,) = rows_at(db_path)
    assert (row["stream"], row["complete"], row["truncated"]) == (1, 1, 0)
    assert (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"]) == (86, 5, 1920)


def test_streaming_without_usage_chunk_records_null_usage_and_never_injects_options(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pcg.db"
    content = fixture_bytes("stream_no_usage.sse")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=ChunkedAsyncStream(content)
        )

    with client_for(handler, db_path) as client:
        response = client.post(
            "/v1/chat/completions", content=request_body(stream=True), headers=AUTH
        )

    (row,) = rows_at(db_path)
    assert response.content == content
    assert seen[0].content == request_body(stream=True)  # request bytes untouched
    assert row["complete"] == 1
    assert row["input_tokens"] is None
    assert row["output_tokens"] is None


def test_prefix_lineage_and_divergence_work_for_openai_chat(tmp_path: Path) -> None:
    db_path = tmp_path / "pcg.db"
    first = request_body()
    second = first.replace(
        b'{"role":"user","content":"Hello"}]',
        b'{"role":"user","content":"Hello"},{"role":"assistant","content":"Hi"},'
        b'{"role":"user","content":"More"}]',
    )

    with client_for(lambda request: httpx.Response(200, content=b"{}"), db_path) as client:
        client.post("/v1/chat/completions", content=first, headers=AUTH)
        client.post("/v1/chat/completions", content=second, headers=AUTH)

    one, two = rows_at(db_path)
    assert (two["predecessor_id"], two["lineage_source"]) == (one["id"], "inferred")
    assert (two["divergence_index"], two["divergence_kind"]) == (2, "append")
