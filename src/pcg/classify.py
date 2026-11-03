"""Classify every request's cache outcome (REQ-BD-06).

Rules are selected by the adapter's ``cache_mode`` (ADR-013). Provider-reported usage is the
truth for whether caching happened; hashes, lineage and timing only explain it. Only hashes,
counts and timestamps are read, never prompt content.

Deliberate approximations, each revisited in M3.2 if the diagnosis rules need more:
- ``truncated`` divergence is treated as an intact prefix, so it never counts as a bust here.
- Without per-segment token counts, AUTO_PREFIX estimates the prefix before a divergence from
  segment byte length (``BYTES_PER_TOKEN``).
- A divergence that explains a miss wins over elapsed time: ``BUST`` before ``TTL_EXPIRED``.
- OpenAI-style lifetime is only a lower bound (ADR-017), so AUTO_PREFIX needs a gap beyond the
  configured upper bound before it says ``TTL_EXPIRED``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pcg.adapters.types import CacheMode, CacheProfile, UsageReporting
from pcg.diff import Divergence, DivergenceKind
from pcg.store import PredecessorSummary, RequestRecord, RequestStore, SegmentRow

BYTES_PER_TOKEN = 4  # rough heuristic, AUTO_PREFIX only


class CacheClass(StrEnum):
    HEALTHY = "HEALTHY"
    COLD_START = "COLD_START"
    TTL_EXPIRED = "TTL_EXPIRED"
    BELOW_MIN_LENGTH = "BELOW_MIN_LENGTH"
    BUST = "BUST"
    UNEXPLAINED = "UNEXPLAINED"
    NOT_REPORTED = "NOT_REPORTED"


@dataclass(frozen=True)
class ClassifyConfig:
    profile: CacheProfile
    ttl_s: int  # EXPLICIT_BREAKPOINT: assumed provider cache TTL (PCG_PROVIDER_CACHE_TTL_S)
    ttl_max_s: int  # AUTO_PREFIX: gap beyond which an entry is surely gone (ADR-017)


_CHANGED = frozenset({DivergenceKind.SERIALIZATION, DivergenceKind.CONTENT})


def _gap_s(record: RequestRecord, predecessor: PredecessorSummary) -> float:
    return (
        datetime.fromisoformat(record.created_at) - datetime.fromisoformat(predecessor.created_at)
    ).total_seconds()


def _predecessor_cached(predecessor: PredecessorSummary, mode: CacheMode) -> int | None:
    if mode is CacheMode.EXPLICIT_BREAKPOINT:
        if predecessor.cache_read_tokens is None and predecessor.cache_write_tokens is None:
            return None
        return (predecessor.cache_read_tokens or 0) + (predecessor.cache_write_tokens or 0)
    return predecessor.cache_read_tokens


def classify(
    record: RequestRecord,
    predecessor: PredecessorSummary | None,
    divergence: Divergence | None,
    segment_rows: Sequence[SegmentRow],
    config: ClassifyConfig,
) -> CacheClass | None:
    """None when the request failed upstream, since there is no cache outcome to explain."""
    status = record.upstream_status
    if status is None or not 200 <= status < 300:
        return None
    profile = config.profile
    read = record.cache_read_tokens
    if (
        profile.cache_mode is CacheMode.NONE
        or profile.usage_reporting is UsageReporting.NONE
        or read is None
    ):
        return CacheClass.NOT_REPORTED
    if predecessor is None:
        return CacheClass.COLD_START

    changed = divergence is not None and divergence.kind in _CHANGED
    cached_before = _predecessor_cached(predecessor, profile.cache_mode)
    reduced = cached_before is not None and read < cached_before
    gap = _gap_s(record, predecessor)
    minimum = profile.min_cacheable_tokens(record.model)
    below_min = (
        record.input_tokens is not None
        and record.input_tokens + read + (record.cache_write_tokens or 0) < minimum
    )

    if profile.cache_mode is CacheMode.EXPLICIT_BREAKPOINT:
        breakpoints = [row.idx for row in segment_rows if row.has_breakpoint]
        last_breakpoint = max(breakpoints) if breakpoints else None
        broken = (
            changed
            and divergence is not None
            and divergence.index is not None
            and last_breakpoint is not None
            and divergence.index <= last_breakpoint
        )
        if read > 0:
            return CacheClass.BUST if broken and reduced else CacheClass.HEALTHY
        if below_min:
            return CacheClass.BELOW_MIN_LENGTH
        if broken:
            return CacheClass.BUST
        if last_breakpoint is not None and gap > config.ttl_s:
            return CacheClass.TTL_EXPIRED
        return CacheClass.UNEXPLAINED

    # AUTO_PREFIX
    if read > 0:
        return CacheClass.BUST if changed and reduced else CacheClass.HEALTHY
    if below_min:
        return CacheClass.BELOW_MIN_LENGTH
    if changed and divergence is not None and divergence.index is not None:
        prefix_bytes = sum(row.byte_len for row in segment_rows if row.idx < divergence.index)
        if prefix_bytes // BYTES_PER_TOKEN < minimum:
            return CacheClass.BUST
    if gap > config.ttl_max_s:
        return CacheClass.TTL_EXPIRED
    return CacheClass.UNEXPLAINED


def resolve_classification(
    store: RequestStore,
    record: RequestRecord,
    divergence: Divergence | None,
    segment_rows: Sequence[SegmentRow],
    config: ClassifyConfig,
) -> CacheClass | None:
    predecessor = (
        store.predecessor_summary(record.predecessor_id)
        if record.predecessor_id is not None
        else None
    )
    return classify(record, predecessor, divergence, segment_rows, config)


__all__ = ["BYTES_PER_TOKEN", "CacheClass", "ClassifyConfig", "classify", "resolve_classification"]
