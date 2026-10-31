"""Live Gate G1 checks against the real Anthropic API (``pytest -m integration --anthropic-live``).

These make a handful of tiny paid requests. They need ``PCG_ANTHROPIC_API_KEY`` in the
environment (a repo secret in ``integration.yml``); the key is only ever sent to Anthropic
as the client's ``x-api-key`` header and is never printed or written anywhere.
"""

import json
import logging
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from pcg.config import Settings
from pcg.gateway import create_app
from pcg.store import requests, segments

pytestmark = pytest.mark.integration

BASE_URL = "https://api.anthropic.com"
MODEL = "claude-sonnet-5-5"  # 512-token minimum cacheable prefix (docs/API.md)
SALT = "x" * 32
ANTHROPIC_VERSION = "2023-06-01"


@pytest.fixture(scope="module")
def api_key() -> str:
    key = os.environ.get("PCG_ANTHROPIC_API_KEY", "")
    if not key:
        pytest.fail("PCG_ANTHROPIC_API_KEY is not set; live checks need a real key")
    return key


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "pcg-live.db"


@pytest.fixture
def client(db_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        upstreams={
            "primary": {
                "wire_format": "anthropic-messages",
                "base_url": BASE_URL,
                "auth_style": "x-api-key",
            }
        },
        default_upstream="primary",
        tenant_hash_salt=SALT,
    )
    app = create_app(settings, db_url=f"sqlite:///{db_path.as_posix()}")
    with TestClient(app) as test_client:
        yield test_client


def headers(key: str, **extra: str) -> dict[str, str]:
    return {
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
        **extra,
    }


def cached_body(nonce: str, *, stream: bool = False, question: str = "Reply with OK.") -> bytes:
    # A unique nonce makes the first request a clean cache write, and ~1.5k tokens of text
    # clears the model's minimum cacheable length.
    filler = " ".join(["The quick brown fox jumps over the lazy dog."] * 140)
    payload = {
        "model": MODEL,
        "max_tokens": 16,
        "stream": stream,
        "system": [
            {
                "type": "text",
                "text": f"[run {nonce}] {filler}",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": question}],
    }
    return json.dumps(payload).encode()


def request_rows(db_path: Path) -> list[dict[str, object]]:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as connection:
            query = select(requests).order_by(requests.c.created_at)
            return [dict(row) for row in connection.execute(query).mappings()]
    finally:
        engine.dispose()


def segment_rows(db_path: Path) -> list[dict[str, object]]:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(segments)).mappings()]
    finally:
        engine.dispose()


def test_cache_write_then_read_is_recorded_and_linked(
    client: TestClient, api_key: str, db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    nonce = uuid.uuid4().hex

    first = client.post("/v1/messages", content=cached_body(nonce), headers=headers(api_key))
    second = client.post("/v1/messages", content=cached_body(nonce), headers=headers(api_key))

    assert first.status_code == 200, first.text[:200]
    assert second.status_code == 200, second.text[:200]
    assert first.json()["type"] == "message"

    one, two = request_rows(db_path)
    assert one["upstream_status"] == 200
    assert one["cache_write_tokens"] is not None and one["cache_write_tokens"] > 0
    assert two["cache_read_tokens"] is not None and two["cache_read_tokens"] > 0
    assert (two["predecessor_id"], two["lineage_source"]) == (one["id"], "inferred")
    assert two["divergence_kind"] == "none"
    assert len(segment_rows(db_path)) == 4  # one system block + one message, per request

    # Gate G1 security: the key is not in the DB, the logs or either response body.
    assert api_key.encode() not in db_path.read_bytes()
    assert all(api_key not in record.getMessage() for record in caplog.records)
    assert api_key not in first.text
    assert api_key not in second.text


def test_streaming_response_is_complete_and_recorded(
    client: TestClient, api_key: str, db_path: Path
) -> None:
    nonce = uuid.uuid4().hex

    response = client.post(
        "/v1/messages", content=cached_body(nonce, stream=True), headers=headers(api_key)
    )

    assert response.status_code == 200
    assert b"event: message_start" in response.content
    assert b"event: message_stop" in response.content
    (row,) = request_rows(db_path)
    assert row["stream"] == 1
    assert row["complete"] == 1
    assert row["truncated"] == 0
    assert isinstance(row["input_tokens"], int)
    assert isinstance(row["output_tokens"], int) and row["output_tokens"] > 0


def test_upstream_error_is_passed_through_and_recorded_with_null_usage(
    client: TestClient, api_key: str, db_path: Path
) -> None:
    body = json.dumps(
        {
            "model": "claude-does-not-exist-live-check",
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "hi"}],
        }
    ).encode()

    response = client.post("/v1/messages", content=body, headers=headers(api_key))

    assert 400 <= response.status_code < 500
    assert response.json()["type"] == "error"
    (row,) = request_rows(db_path)
    assert row["upstream_status"] == response.status_code
    assert row["input_tokens"] is None
    assert row["output_tokens"] is None


def test_wrong_key_is_rejected_by_upstream_not_by_the_gateway(
    client: TestClient, db_path: Path
) -> None:
    response = client.post(
        "/v1/messages",
        content=cached_body(uuid.uuid4().hex),
        headers=headers("invalid-live-check-key"),
    )

    assert response.status_code == 401
    (row,) = request_rows(db_path)
    assert row["upstream_status"] == 401
    assert b"invalid-live-check-key" not in db_path.read_bytes()
