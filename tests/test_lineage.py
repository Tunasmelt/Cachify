from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect

from pcg.lineage import (
    best_candidate,
    common_prefix_len,
    insert_with_lineage,
    resolve_predecessor,
)
from pcg.store import RequestRecord, RequestStore, SegmentRow, requests

pytestmark = pytest.mark.detector

T0 = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
WINDOW = {"window_requests": 50, "window_s": 3600}


def record(
    request_id: str,
    *,
    at: datetime = T0,
    tenant: str = "tenant-a",
    model: str = "model-x",
    session: str | None = None,
) -> RequestRecord:
    return RequestRecord(
        id=request_id,
        created_at=at.isoformat(),
        tenant_hash=tenant,
        session_id=session,
        upstream="primary",
        wire_format="anthropic-messages",
        cache_mode="explicit_breakpoint",
        model=model,
        stream=0,
        upstream_status=200,
        complete=None,
        truncated=0,
        input_tokens=None,
        output_tokens=None,
        cache_write_tokens=None,
        cache_read_tokens=None,
        latency_ms=1.0,
        log_mode="hash",
    )


def rows(request_id: str, hashes: Sequence[str]) -> list[SegmentRow]:
    return [
        SegmentRow(request_id, idx, "message", "user", h, h, 1, False)
        for idx, h in enumerate(hashes)
    ]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RequestStore]:
    db = RequestStore(f"sqlite:///{(tmp_path / 'pcg.db').as_posix()}")
    db.migrate()
    yield db
    db.dispose()


def add(
    store: RequestStore,
    request_id: str,
    hashes: Sequence[str],
    *,
    at: datetime = T0,
    tenant: str = "tenant-a",
    model: str = "model-x",
    session: str | None = None,
) -> None:
    store.insert(
        record(request_id, at=at, tenant=tenant, model=model, session=session),
        rows(request_id, hashes),
    )


def resolve(
    store: RequestStore, new: RequestRecord, hashes: Sequence[str]
) -> tuple[str, str] | None:
    found = resolve_predecessor(store, new, rows(new.id, hashes), **WINDOW)
    return (found.request_id, found.source) if found else None


def test_common_prefix_len_and_best_candidate() -> None:
    assert common_prefix_len(["a", "b", "c"], ["a", "b", "x"]) == 2
    assert common_prefix_len(["a"], ["b"]) == 0
    assert best_candidate(["a", "b"], [("n", ["a"]), ("o", ["a", "b"])]) == "o"
    assert best_candidate(["a"], [("n", ["z"])]) is None


def test_inference_picks_longest_common_prefix(store: RequestStore) -> None:
    add(store, "short", ["a", "b"], at=T0 - timedelta(minutes=5))
    add(store, "long", ["a", "b", "c"], at=T0 - timedelta(minutes=10))

    assert resolve(store, record("new"), ["a", "b", "c", "d"]) == ("long", "inferred")


def test_inference_tie_goes_to_most_recent(store: RequestStore) -> None:
    add(store, "older", ["a", "b"], at=T0 - timedelta(minutes=20))
    add(store, "newer", ["a", "b"], at=T0 - timedelta(minutes=2))

    assert resolve(store, record("new"), ["a", "b", "c"]) == ("newer", "inferred")


def test_inference_requires_at_least_one_shared_segment(store: RequestStore) -> None:
    add(store, "other", ["x", "y"], at=T0 - timedelta(minutes=1))

    assert resolve(store, record("new"), ["a", "b"]) is None


def test_inference_with_no_segments_has_no_predecessor(store: RequestStore) -> None:
    add(store, "other", ["a"], at=T0 - timedelta(minutes=1))

    assert resolve(store, record("new"), []) is None


def test_inference_is_strict_on_tenant_and_model(store: RequestStore) -> None:
    add(store, "other-tenant", ["a", "b"], tenant="tenant-b", at=T0 - timedelta(minutes=1))
    add(store, "other-model", ["a", "b"], model="model-y", at=T0 - timedelta(minutes=1))

    assert resolve(store, record("new"), ["a", "b"]) is None


def test_inference_ignores_requests_outside_time_window(store: RequestStore) -> None:
    add(store, "stale", ["a", "b"], at=T0 - timedelta(hours=1, seconds=1))
    assert resolve(store, record("new"), ["a", "b"]) is None

    add(store, "edge", ["a"], at=T0 - timedelta(hours=1))
    assert resolve(store, record("new2"), ["a", "b"]) == ("edge", "inferred")


def test_inference_only_looks_at_last_n_requests(store: RequestStore) -> None:
    add(store, "best", ["a", "b", "c"], at=T0 - timedelta(minutes=59))
    for i in range(3):
        add(store, f"filler{i}", ["a"], at=T0 - timedelta(minutes=10 - i))
    new = record("new")

    narrow = resolve_predecessor(
        store, new, rows("new", ["a", "b", "c"]), window_requests=3, window_s=3600
    )
    wide = resolve_predecessor(
        store, new, rows("new", ["a", "b", "c"]), window_requests=4, window_s=3600
    )

    assert narrow is not None and narrow.request_id == "filler2"
    assert wide is not None and wide.request_id == "best"


def test_session_header_wins_over_better_prefix_match(store: RequestStore) -> None:
    add(store, "match", ["a", "b", "c"], at=T0 - timedelta(minutes=1))
    add(store, "s1", ["q"], session="s", at=T0 - timedelta(minutes=30))
    add(store, "s2", ["z"], session="s", at=T0 - timedelta(minutes=10))

    assert resolve(store, record("new", session="s"), ["a", "b", "c"]) == ("s2", "session")


def test_session_lookup_is_tenant_scoped(store: RequestStore) -> None:
    add(store, "theirs", ["a"], session="s", tenant="tenant-b")

    assert resolve(store, record("new", session="s"), ["a"]) is None


def test_session_lineage_crosses_models_and_ignores_window(store: RequestStore) -> None:
    add(store, "old", ["a"], session="s", model="model-y", at=T0 - timedelta(days=2))

    assert resolve(store, record("new", session="s"), ["a"]) == ("old", "session")


def test_session_with_no_history_is_cold_start(store: RequestStore) -> None:
    assert resolve(store, record("new", session="fresh"), ["a"]) is None


def test_insert_with_lineage_persists_predecessor(store: RequestStore) -> None:
    add(store, "first", ["a", "b"], at=T0 - timedelta(minutes=1))

    insert_with_lineage(store, record("second"), rows("second", ["a", "b", "c"]), **WINDOW)

    with store.engine.connect() as connection:
        row = (
            connection.execute(requests.select().where(requests.c.id == "second")).mappings().one()
        )
    assert (row["predecessor_id"], row["lineage_source"]) == ("first", "inferred")


def test_lineage_failure_never_blocks_insert(
    store: RequestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("pcg.lineage.resolve_predecessor", boom)
    insert_with_lineage(store, record("only"), rows("only", ["a"]), **WINDOW)

    assert store.count() == 1
    with store.engine.connect() as connection:
        row = connection.execute(requests.select()).mappings().one()
    assert row["predecessor_id"] is None


def test_migration_matches_requests_table_columns(store: RequestStore) -> None:
    columns = {c["name"]: c for c in inspect(store.engine).get_columns("requests")}

    assert set(columns) == {c.name for c in requests.columns}
    assert columns["predecessor_id"]["nullable"]
    assert columns["lineage_source"]["nullable"]
