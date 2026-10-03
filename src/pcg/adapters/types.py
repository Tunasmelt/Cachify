from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from pcg.config import AuthStyle, WireFormat

__all__ = [
    "AuthStyle",
    "CacheMode",
    "CacheProfile",
    "CanonicalRequest",
    "Segment",
    "SegmentKind",
    "Usage",
    "UsageReporting",
    "WireFormat",
]


class SegmentKind(StrEnum):
    TOOL = "tool"
    SYSTEM = "system"
    MESSAGE = "message"


@dataclass(frozen=True)
class Segment:
    kind: SegmentKind
    idx: int
    role: str | None
    raw: bytes
    has_breakpoint: bool


@dataclass(frozen=True)
class CanonicalRequest:
    model: str
    stream: bool
    segments: tuple[Segment, ...]
    params: dict[str, object]


class CacheMode(StrEnum):
    EXPLICIT_BREAKPOINT = "explicit_breakpoint"
    AUTO_PREFIX = "auto_prefix"
    NONE = "none"


class UsageReporting(StrEnum):
    FULL = "full"
    READ_ONLY = "read_only"
    NONE = "none"


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int | None
    cache_read_tokens: int | None


@dataclass(frozen=True)
class CacheProfile:
    cache_mode: CacheMode
    min_cacheable_tokens: Callable[[str], int]
    default_ttl_s: int
    usage_reporting: UsageReporting
    segment_order: tuple[SegmentKind, ...]
