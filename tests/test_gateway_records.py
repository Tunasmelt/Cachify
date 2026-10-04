import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from pcg.config import Settings
from pcg.gateway import create_app
from pcg.records import tenant_hash_for
from pcg.store import RequestStore, requests
from tests.test_gateway_streaming import fixture_bytes, stream_handler

pytestmark = pytest.mark.proxy
BASE_URL = "https://provider.example.invalid"
SALT = "x" * 32


def make_settings(*, salt: str | None = SALT) -> Settings:
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


def rows_at(path: Path) -> list[dict[str, object]]:
    engine = create_engine(sqlite_url(path))
    try:
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(requests)).mappings()]
    finally:
        engine.dispose()


def test_non_stream_usage_is_recorded_and_key_is_absent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db_path = tmp_path / "pcg.db"
    sentinel = "test-client-key-sentinel"
    body = (Path(__file__).parent / "fixtures" / "anthropic" / "response_plain.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    caplog.set_level(logging.DEBUG)
    app = create_app(
        make_settings(), transport=httpx.MockTransport(handler), db_url=sqlite_url(db_path)
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages",
            content=request_body(),
            headers={"x-api-key": sentinel, "x-pcg-session": "session"},
        )

    row = rows_at(db_path)[0]
    assert response.content == body
    assert row["input_tokens"] == 20
    assert row["output_tokens"] == 2
    assert row["cache_write_tokens"] == 10
    assert row["cache_read_tokens"] == 5
    assert row["upstream_status"] == 200
    assert row["stream"] == 0
    assert row["upstream"] == "primary"
    assert row["wire_format"] == "anthropic-messages"
    assert row["cache_mode"] == "explicit_breakpoint"
    assert row["tenant_hash"] == tenant_hash_for(SALT.encode(), sentinel)
    assert row["tenant_hash"] != sentinel
    assert sentinel.encode() not in db_path.read_bytes()
    assert all(sentinel not in record.getMessage() for record in caplog.records)


def test_non_stream_error_records_null_usage(tmp_path: Path) -> None:
    db_path = tmp_path / "pcg.db"

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, content=b'{"type":"error"}')

    app = create_app(
        make_settings(), transport=httpx.MockTransport(handler), db_url=sqlite_url(db_path)
    )
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body())

    row = rows_at(db_path)[0]
    assert response.status_code == 429
    assert row["upstream_status"] == 429
    for field in (
        "input_tokens",
        "output_tokens",
        "cache_write_tokens",
        "cache_read_tokens",
    ):
        assert row[field] is None


@pytest.mark.parametrize(
    ("fixture", "complete"),
    [("stream_plain.sse", 1), ("stream_truncated.sse", 0)],
)
def test_stream_record_has_usage_and_completion(
    tmp_path: Path, fixture: str, complete: int
) -> None:
    db_path = tmp_path / "pcg.db"
    content = fixture_bytes(fixture)
    app = create_app(
        make_settings(),
        transport=httpx.MockTransport(stream_handler(content)),
        db_url=sqlite_url(db_path),
    )
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body(stream=True))

    row = rows_at(db_path)[0]
    assert response.content == content
    assert row["stream"] == 1
    assert row["complete"] == complete
    assert row["truncated"] == 0
    assert row["input_tokens"] == (42 if complete else 27)
    assert row["output_tokens"] == (7 if complete else 4)
    assert row["cache_write_tokens"] == (11 if complete else 5)
    assert row["cache_read_tokens"] == (23 if complete else 9)


def test_streaming_upstream_error_is_recorded(tmp_path: Path) -> None:
    db_path = tmp_path / "pcg.db"

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, content=b'{"type":"error"}')

    app = create_app(
        make_settings(), transport=httpx.MockTransport(handler), db_url=sqlite_url(db_path)
    )
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body(stream=True))

    row = rows_at(db_path)[0]
    assert response.status_code == 429
    assert row["upstream_status"] == 429
    assert row["complete"] is None
    assert row["input_tokens"] is None


def test_transport_error_records_null_status_and_returns_502(tmp_path: Path) -> None:
    db_path = tmp_path / "pcg.db"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic connection failure", request=request)

    app = create_app(
        make_settings(), transport=httpx.MockTransport(handler), db_url=sqlite_url(db_path)
    )
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body())

    assert response.status_code == 502
    assert rows_at(db_path)[0]["upstream_status"] is None


def test_salt_unset_disables_database_records(tmp_path: Path) -> None:
    db_path = tmp_path / "pcg.db"

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"{}")

    app = create_app(
        make_settings(salt=None),
        transport=httpx.MockTransport(handler),
        db_url=sqlite_url(db_path),
    )
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body())

    assert response.status_code == 200
    assert not db_path.exists()


def test_insert_failure_does_not_change_response_or_stop_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "pcg.db"
    body = b'{"type":"message","usage":{"input_tokens":1,"output_tokens":2}}'

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=body)

    def fail_insert(self: RequestStore, record: object) -> None:
        del self, record
        raise RuntimeError("synthetic insert failure")

    monkeypatch.setattr(RequestStore, "insert", fail_insert)
    app = create_app(
        make_settings(), transport=httpx.MockTransport(handler), db_url=sqlite_url(db_path)
    )
    with TestClient(app) as client:
        first = client.post("/v1/messages", content=request_body())
        second = client.post("/v1/messages", content=request_body())

    assert (first.status_code, first.content) == (200, body)
    assert (second.status_code, second.content) == (200, body)


def test_migration_failure_does_not_stop_proxy(tmp_path: Path) -> None:
    directory_path = tmp_path / "database-directory"
    directory_path.mkdir()

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"{}")

    app = create_app(
        make_settings(),
        transport=httpx.MockTransport(handler),
        db_url=sqlite_url(directory_path),
    )
    with TestClient(app) as client:
        response = client.post("/v1/messages", content=request_body())

    assert response.status_code == 200
    assert directory_path.is_dir()
