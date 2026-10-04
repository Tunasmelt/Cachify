from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from pcg.store import RequestRecord, RequestStore

pytestmark = pytest.mark.proxy


def make_record() -> RequestRecord:
    return RequestRecord(
        id="request-id",
        created_at="2026-10-05T00:00:00+00:00",
        tenant_hash="tenant-hash",
        session_id=None,
        upstream="primary",
        wire_format="anthropic-messages",
        cache_mode="explicit_breakpoint",
        model="model",
        stream=0,
        upstream_status=200,
        complete=None,
        truncated=0,
        input_tokens=10,
        output_tokens=2,
        cache_write_tokens=None,
        cache_read_tokens=None,
        latency_ms=2.5,
        log_mode="hash",
    )


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_migrate_creates_requests_table_and_insert_counts(tmp_path: Path) -> None:
    store = RequestStore(sqlite_url(tmp_path / "pcg.db"))
    try:
        store.migrate()
        store.insert(make_record())

        assert "requests" in inspect(store.engine).get_table_names()
        assert store.count() == 1
    finally:
        store.dispose()


def test_duplicate_request_id_raises_integrity_error(tmp_path: Path) -> None:
    store = RequestStore(sqlite_url(tmp_path / "pcg.db"))
    try:
        store.migrate()
        store.insert(make_record())

        with pytest.raises(IntegrityError):
            store.insert(make_record())
    finally:
        store.dispose()
