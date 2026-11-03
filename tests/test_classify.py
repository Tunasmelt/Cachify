from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from pcg.adapters.anthropic import AnthropicAdapter
from pcg.adapters.openai_chat import OpenAIChatAdapter
from pcg.adapters.types import CacheMode, CacheProfile, SegmentKind, UsageReporting
from pcg.classify import CacheClass, ClassifyConfig, classify
from pcg.diff import Divergence, DivergenceKind
from pcg.lineage import insert_with_lineage
from pcg.store import PredecessorSummary, RequestRecord, RequestStore, SegmentRow, requests

pytestmark = pytest.mark.classify

T0 = datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)
ANTHROPIC = ClassifyConfig(AnthropicAdapter().cache_profile(), ttl_s=300, ttl_max_s=86400)
OPENAI = ClassifyConfig(OpenAIChatAdapter().cache_profile(), ttl_s=300, ttl_max_s=86400)
NO_CACHE = ClassifyConfig(
    CacheProfile(
        cache_mode=CacheMode.NONE,
        min_cacheable_tokens=lambda model: 1024,
        default_ttl_s=300,
        usage_reporting=UsageReporting.NONE,
        segment_order=(SegmentKind.SYSTEM, SegmentKind.MESSAGE),
    ),
    ttl_s=300,
    ttl_max_s=86400,
)


def request(
    *,
    read: int | None = 0,
    write: int | None = 0,
    input_tokens: int | None = 5000,
    gap_s: float = 10,
    status: int | None = 200,
    mode: str = "explicit_breakpoint",
    latency_ms: float = 0.0,
    complete: int | None = None,
    truncated: int = 0,
) -> RequestRecord:
    return RequestRecord(
        id="r2",
        created_at=(T0 + timedelta(seconds=gap_s)).isoformat(),
        tenant_hash="t",
        session_id=None,
        upstream="u",
        wire_format="anthropic-messages",
        cache_mode=mode,
        model="claude-sonnet-5-5",
        stream=0,
        upstream_status=status,
        complete=complete,
        truncated=truncated,
        input_tokens=input_tokens,
        output_tokens=10,
        cache_write_tokens=write,
        cache_read_tokens=read,
        latency_ms=latency_ms,
        log_mode="hash",
    )


def previous(
    read: int | None = 0, write: int | None = 0, latency_ms: float = 0.0
) -> PredecessorSummary:
    return PredecessorSummary(T0.isoformat(), read, write, latency_ms)


def rows(*, breakpoint_at: int | None = None, sizes: Sequence[int] = (100, 100, 100)) -> list:
    return [
        SegmentRow("r2", idx, "message", "user", f"r{idx}", f"c{idx}", size, idx == breakpoint_at)
        for idx, size in enumerate(sizes)
    ]


def diverged(index: int, kind: DivergenceKind = DivergenceKind.CONTENT) -> Divergence:
    return Divergence(index, kind)


APPEND = Divergence(3, DivergenceKind.APPEND)
SAME = Divergence(None, DivergenceKind.NONE)


# --- common to every mode -------------------------------------------------------------------


def test_failed_upstream_request_is_not_classified() -> None:
    assert classify(request(status=429), previous(), SAME, rows(), ANTHROPIC) is None
    assert classify(request(status=None), previous(), SAME, rows(), ANTHROPIC) is None


def test_provider_that_reports_nothing_is_not_reported() -> None:
    assert classify(request(), previous(), SAME, rows(), NO_CACHE) is CacheClass.NOT_REPORTED


@pytest.mark.parametrize("fields", [{"truncated": 1}, {"complete": 0}])
def test_cut_off_stream_without_usage_is_not_classified(fields: dict[str, int]) -> None:
    for config in (ANTHROPIC, OPENAI):
        record = request(read=None, **fields)
        assert classify(record, previous(), SAME, rows(), config) is None


def test_cut_off_stream_that_still_reported_usage_is_classified() -> None:
    result = classify(
        request(read=4000, complete=0), previous(write=4000), APPEND, rows(), ANTHROPIC
    )
    assert result is CacheClass.HEALTHY


def test_gap_is_measured_between_request_starts_not_completions() -> None:
    # Both requests finished 310 s apart, but the second streamed for 20 s: its start was 290 s
    # after the predecessor's, inside the 300 s TTL.
    slow = request(read=0, write=4000, gap_s=310, latency_ms=20_000)
    within = classify(slow, previous(write=4000), APPEND, rows(breakpoint_at=1), ANTHROPIC)
    assert within is CacheClass.UNEXPLAINED
    fast = request(read=0, write=4000, gap_s=310)
    expired = classify(fast, previous(write=4000), APPEND, rows(breakpoint_at=1), ANTHROPIC)
    assert expired is CacheClass.TTL_EXPIRED


def test_predecessor_latency_moves_its_start_earlier() -> None:
    # Predecessor completed at T0 after 30 s, so it started 30 s earlier: the gap grows by 30 s.
    record = request(read=0, write=4000, gap_s=280)
    result = classify(
        record, previous(write=4000, latency_ms=30_000), APPEND, rows(breakpoint_at=1), ANTHROPIC
    )
    assert result is CacheClass.TTL_EXPIRED


def test_unknown_cache_read_is_not_reported_not_zero() -> None:
    result = classify(request(read=None), previous(), SAME, rows(), OPENAI)
    assert result is CacheClass.NOT_REPORTED


def test_no_predecessor_is_cold_start() -> None:
    assert classify(request(), None, None, rows(), ANTHROPIC) is CacheClass.COLD_START
    assert classify(request(), None, None, rows(), OPENAI) is CacheClass.COLD_START


def test_not_reported_wins_over_cold_start() -> None:
    assert classify(request(), None, None, rows(), NO_CACHE) is CacheClass.NOT_REPORTED


# --- EXPLICIT_BREAKPOINT (anthropic) ---------------------------------------------------------


def test_explicit_cache_read_is_healthy() -> None:
    result = classify(request(read=4000), previous(write=4000), APPEND, rows(), ANTHROPIC)
    assert result is CacheClass.HEALTHY


def test_explicit_miss_after_change_before_breakpoint_is_bust() -> None:
    result = classify(
        request(read=0, write=4000),
        previous(write=4000),
        diverged(0),
        rows(breakpoint_at=2),
        ANTHROPIC,
    )
    assert result is CacheClass.BUST


def test_explicit_serialization_only_change_is_still_a_bust() -> None:
    result = classify(
        request(read=0),
        previous(write=4000),
        diverged(1, DivergenceKind.SERIALIZATION),
        rows(breakpoint_at=2),
        ANTHROPIC,
    )
    assert result is CacheClass.BUST


def test_explicit_reduced_read_after_change_is_bust() -> None:
    result = classify(
        request(read=1000),
        previous(read=4000),
        diverged(1),
        rows(breakpoint_at=2),
        ANTHROPIC,
    )
    assert result is CacheClass.BUST


def test_explicit_change_after_the_last_breakpoint_does_not_bust() -> None:
    result = classify(
        request(read=4000),
        previous(write=4000),
        diverged(2),
        rows(breakpoint_at=1),
        ANTHROPIC,
    )
    assert result is CacheClass.HEALTHY


def test_explicit_gap_past_ttl_with_prefix_intact_is_ttl_expired() -> None:
    result = classify(
        request(read=0, write=4000, gap_s=301),
        previous(write=4000),
        APPEND,
        rows(breakpoint_at=1),
        ANTHROPIC,
    )
    assert result is CacheClass.TTL_EXPIRED


def test_explicit_gap_within_ttl_with_prefix_intact_is_unexplained() -> None:
    result = classify(
        request(read=0, write=4000, gap_s=299),
        previous(write=4000),
        APPEND,
        rows(breakpoint_at=1),
        ANTHROPIC,
    )
    assert result is CacheClass.UNEXPLAINED


def test_explicit_change_explains_the_miss_even_past_ttl() -> None:
    result = classify(
        request(read=0, gap_s=9999),
        previous(write=4000),
        diverged(0),
        rows(breakpoint_at=2),
        ANTHROPIC,
    )
    assert result is CacheClass.BUST


def test_explicit_prompt_under_model_minimum_is_below_min_length() -> None:
    result = classify(
        request(read=0, write=0, input_tokens=100),
        previous(),
        APPEND,
        rows(breakpoint_at=1),
        ANTHROPIC,
    )
    assert result is CacheClass.BELOW_MIN_LENGTH


def test_explicit_without_breakpoints_never_claims_ttl_expiry() -> None:
    result = classify(request(read=0, gap_s=9999), previous(write=4000), APPEND, rows(), ANTHROPIC)
    assert result is CacheClass.UNEXPLAINED


# --- AUTO_PREFIX (openai-chat) ---------------------------------------------------------------


def test_auto_prefix_cache_read_is_healthy() -> None:
    result = classify(
        request(read=3000, write=None), previous(read=0, write=None), APPEND, rows(), OPENAI
    )
    assert result is CacheClass.HEALTHY


def test_auto_prefix_reduced_read_after_change_is_bust() -> None:
    result = classify(
        request(read=1024, write=None),
        previous(read=4096, write=None),
        diverged(2),
        rows(),
        OPENAI,
    )
    assert result is CacheClass.BUST


def test_auto_prefix_miss_after_early_change_is_bust() -> None:
    result = classify(
        request(read=0, write=None, input_tokens=5000),
        previous(read=0, write=None),
        diverged(0),
        rows(sizes=(40_000, 100, 100)),
        OPENAI,
    )
    assert result is CacheClass.BUST


def test_auto_prefix_miss_despite_long_intact_prefix_is_not_blamed_on_the_change() -> None:
    result = classify(
        request(read=0, write=None, input_tokens=20_000),
        previous(read=0, write=None),
        diverged(2),
        rows(sizes=(40_000, 40_000, 100)),
        OPENAI,
    )
    assert result is CacheClass.UNEXPLAINED


def test_auto_prefix_ttl_needs_the_upper_bound_not_the_lower() -> None:
    within = classify(
        request(read=0, write=None, gap_s=3600),
        previous(read=0, write=None),
        APPEND,
        rows(),
        OPENAI,
    )
    beyond = classify(
        request(read=0, write=None, gap_s=86401),
        previous(read=0, write=None),
        APPEND,
        rows(),
        OPENAI,
    )
    assert within is CacheClass.UNEXPLAINED
    assert beyond is CacheClass.TTL_EXPIRED


def test_auto_prefix_prompt_under_minimum_is_below_min_length() -> None:
    result = classify(
        request(read=0, write=None, input_tokens=500),
        previous(read=0, write=None),
        APPEND,
        rows(),
        OPENAI,
    )
    assert result is CacheClass.BELOW_MIN_LENGTH


def test_auto_prefix_uses_only_read_tokens_as_the_reduced_baseline() -> None:
    result = classify(
        request(read=1024, write=None),
        previous(read=None, write=None),
        diverged(2),
        rows(),
        OPENAI,
    )
    assert result is CacheClass.HEALTHY


# --- stored through the real insert path -----------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RequestStore]:
    db = RequestStore(f"sqlite:///{tmp_path / 'c.db'}")
    db.migrate()
    yield db
    db.dispose()


def stored(
    request_id: str, at: datetime, **fields: object
) -> tuple[RequestRecord, list[SegmentRow]]:
    base = replace(request(read=0, write=4000, gap_s=0), id=request_id, created_at=at.isoformat())
    rec = replace(base, **fields)  # type: ignore[arg-type]
    segs = [
        SegmentRow(request_id, i, "message", "user", f"r{i}", f"c{i}", 100, i == 1)
        for i in range(2)
    ]
    return rec, segs


def classifications(db: RequestStore) -> dict[str, str | None]:
    with db.engine.connect() as connection:
        return {row.id: row.classification for row in connection.execute(select(requests))}


def test_insert_classifies_cold_start_then_healthy(store: RequestStore) -> None:
    window = {"window_requests": 50, "window_s": 3600}
    first, first_rows = stored("a", T0)
    insert_with_lineage(store, first, first_rows, classify=ANTHROPIC, **window)
    second, second_rows = stored("b", T0 + timedelta(seconds=30), cache_read_tokens=4000)
    insert_with_lineage(store, second, second_rows, classify=ANTHROPIC, **window)
    assert classifications(store) == {"a": "COLD_START", "b": "HEALTHY"}


def test_insert_without_config_leaves_classification_empty(store: RequestStore) -> None:
    first, first_rows = stored("a", T0)
    insert_with_lineage(store, first, first_rows, window_requests=50, window_s=3600)
    assert classifications(store) == {"a": None}


def test_classifier_failure_never_blocks_the_insert(
    store: RequestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("pcg.lineage.resolve_classification", boom)
    first, first_rows = stored("a", T0)
    insert_with_lineage(
        store, first, first_rows, window_requests=50, window_s=3600, classify=ANTHROPIC
    )
    assert classifications(store) == {"a": None}
    assert store.count() == 1


def test_migration_adds_classification_column(store: RequestStore) -> None:
    from sqlalchemy import inspect

    assert "classification" in {c["name"] for c in inspect(store.engine).get_columns("requests")}
