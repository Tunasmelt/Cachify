from dataclasses import replace
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


def test_migrate_creates_segments_table_matching_metadata(tmp_path: Path) -> None:
    from pcg.store import segments

    store = RequestStore(sqlite_url(tmp_path / "pcg.db"))
    try:
        store.migrate()
        columns = {c["name"]: c for c in inspect(store.engine).get_columns("segments")}

        assert set(columns) == {c.name for c in segments.columns}
        assert {n for n, c in columns.items() if not c["nullable"]} == {
            c.name for c in segments.columns if not c.nullable
        }
        assert inspect(store.engine).get_pk_constraint("segments")["constrained_columns"] == [
            "request_id",
            "idx",
        ]
    finally:
        store.dispose()


def test_insert_writes_request_and_segment_rows_atomically(tmp_path: Path) -> None:
    from sqlalchemy import func, select

    from pcg.store import SegmentRow, segments

    def row(idx: int) -> SegmentRow:
        return SegmentRow("request-id", idx, "message", "user", "r" * 64, "c" * 64, 10, False)

    store = RequestStore(sqlite_url(tmp_path / "pcg.db"))
    try:
        store.migrate()
        store.insert(make_record(), [row(0), row(1)])
        with store.engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(segments)) == 2

        with pytest.raises(IntegrityError):
            store.insert(
                replace(make_record(), id="other"),
                [row(0), row(0)],
            )
        with store.engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(segments)) == 2
            assert store.count() == 1
    finally:
        store.dispose()
