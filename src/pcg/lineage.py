"""Lineage: link each request to its predecessor (REQ-BD-03).

An explicit ``x-pcg-session`` wins and picks the latest request in that session for the
same tenant (any model, so a model change inside a session stays diagnosable). Without
one, the predecessor is the recent same-tenant, same-model request sharing the longest
``raw_hash`` prefix; ties go to the most recent. Only hashes and ids are read or stored.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from pcg.diff import Divergence, resolve_divergence
from pcg.failopen import guard
from pcg.store import RequestRecord, RequestStore, SegmentRow

SOURCE_SESSION = "session"
SOURCE_INFERRED = "inferred"


@dataclass(frozen=True)
class Predecessor:
    request_id: str
    source: str


def common_prefix_len(a: Sequence[str], b: Sequence[str]) -> int:
    count = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        count += 1
    return count


def best_candidate(
    raw_hashes: Sequence[str], candidates: Sequence[tuple[str, list[str]]]
) -> str | None:
    """Longest shared prefix (at least one segment); candidates are newest-first so ties keep
    the most recent."""
    best_id: str | None = None
    best_len = 0
    for request_id, candidate_hashes in candidates:
        length = common_prefix_len(raw_hashes, candidate_hashes)
        if length > best_len:
            best_id, best_len = request_id, length
    return best_id


def resolve_predecessor(
    store: RequestStore,
    record: RequestRecord,
    segment_rows: Sequence[SegmentRow],
    *,
    window_requests: int,
    window_s: int,
) -> Predecessor | None:
    if record.session_id:
        found = store.latest_in_session(record.tenant_hash, record.session_id)
        return Predecessor(found, SOURCE_SESSION) if found else None

    raw_hashes = [row.raw_hash for row in sorted(segment_rows, key=lambda r: r.idx)]
    if not raw_hashes:
        return None
    since = (datetime.fromisoformat(record.created_at) - timedelta(seconds=window_s)).isoformat()
    candidates = store.recent_segment_hashes(
        record.tenant_hash, record.model, since, window_requests
    )
    found = best_candidate(raw_hashes, candidates)
    return Predecessor(found, SOURCE_INFERRED) if found else None


def insert_with_lineage(
    store: RequestStore,
    record: RequestRecord,
    segment_rows: Sequence[SegmentRow],
    *,
    window_requests: int,
    window_s: int,
) -> None:
    """Insert the request; a lineage failure never blocks the insert (fail-open)."""
    predecessor: Predecessor | None = guard(
        "lineage",
        resolve_predecessor,
        store,
        record,
        segment_rows,
        window_requests=window_requests,
        window_s=window_s,
        default=None,
    )
    if predecessor is not None:
        record = replace(
            record,
            predecessor_id=predecessor.request_id,
            lineage_source=predecessor.source,
        )
        divergence: Divergence | None = guard(
            "diff",
            resolve_divergence,
            store,
            predecessor.request_id,
            segment_rows,
            default=None,
        )
        if divergence is not None:
            record = replace(
                record,
                divergence_index=divergence.index,
                divergence_kind=divergence.kind.value,
            )
    store.insert(record, segment_rows)


__all__ = [
    "Predecessor",
    "best_candidate",
    "common_prefix_len",
    "insert_with_lineage",
    "resolve_predecessor",
]
