"""Dual segment hashing (ADR-006).

``raw_hash`` covers the exact bytes sent; ``canon_hash`` covers the meaning, so a raw
difference with an equal canonical hash means the cache broke on serialization alone.
"""

import hashlib
import json
from dataclasses import dataclass

from pcg.adapters.types import CanonicalRequest


@dataclass(frozen=True)
class SegmentHashes:
    raw_hash: str
    canon_hash: str
    byte_len: int


def canonical_json(raw: bytes) -> bytes:
    """Sorted keys, no insignificant whitespace, non-ASCII kept literal."""
    return json.dumps(
        json.loads(raw),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def hash_segment(raw: bytes) -> SegmentHashes:
    return SegmentHashes(
        raw_hash=hashlib.sha256(raw).hexdigest(),
        canon_hash=hashlib.sha256(canonical_json(raw)).hexdigest(),
        byte_len=len(raw),
    )


def hash_segments(request: CanonicalRequest) -> tuple[SegmentHashes, ...]:
    return tuple(hash_segment(segment.raw) for segment in request.segments)


__all__ = ["SegmentHashes", "canonical_json", "hash_segment", "hash_segments"]
