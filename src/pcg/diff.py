"""First-divergence detection between a request and its predecessor (REQ-BD-04).

Works on segment hashes only, so it never touches prompt content. ``raw_hash`` decides
where the sent bytes first differ; ``canon_hash`` tells whether that difference changes
the meaning or is serialization noise (ADR-006).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from pcg.store import RequestStore, SegmentRow

HashPair = tuple[str, str]  # (raw_hash, canon_hash)


class DivergenceKind(StrEnum):
    NONE = "none"  # identical segment lists
    APPEND = "append"  # predecessor is a strict prefix; new segments were added
    TRUNCATED = "truncated"  # current is a strict prefix of the predecessor (history trimmed)
    SERIALIZATION = "serialization"  # raw differs, canonical equal
    CONTENT = "content"  # canonical differs


@dataclass(frozen=True)
class Divergence:
    index: int | None
    kind: DivergenceKind


def first_divergence(current: Sequence[HashPair], previous: Sequence[HashPair]) -> Divergence:
    for index, (now, before) in enumerate(zip(current, previous, strict=False)):
        if now[0] != before[0]:
            kind = DivergenceKind.SERIALIZATION if now[1] == before[1] else DivergenceKind.CONTENT
            return Divergence(index, kind)
    if len(current) > len(previous):
        return Divergence(len(previous), DivergenceKind.APPEND)
    if len(current) < len(previous):
        return Divergence(len(current), DivergenceKind.TRUNCATED)
    return Divergence(None, DivergenceKind.NONE)


def resolve_divergence(
    store: RequestStore, predecessor_id: str, segment_rows: Sequence[SegmentRow]
) -> Divergence | None:
    """None when either side has no recorded segments, since nothing can be compared."""
    previous = store.segment_hashes(predecessor_id)
    if not previous or not segment_rows:
        return None
    current = [(row.raw_hash, row.canon_hash) for row in sorted(segment_rows, key=lambda r: r.idx)]
    return first_divergence(current, previous)


__all__ = [
    "Divergence",
    "DivergenceKind",
    "HashPair",
    "first_divergence",
    "resolve_divergence",
]
