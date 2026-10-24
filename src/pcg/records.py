import hashlib
import hmac
from datetime import UTC, datetime
from uuid import uuid4

from pcg.adapters.types import CanonicalRequest, Usage
from pcg.hashing import hash_segments
from pcg.store import RequestRecord, SegmentRow


def tenant_hash_for(salt: bytes, client_key: str) -> str:
    """Hash the client key (including the allowed empty-key case) for tenant scoping."""
    return hmac.new(salt, client_key.encode(), hashlib.sha256).hexdigest()


def build_record(
    *,
    tenant_hash: str,
    session_id: str | None,
    upstream: str,
    wire_format: str,
    cache_mode: str,
    model: str,
    stream: bool,
    upstream_status: int | None,
    complete: bool | None,
    truncated: bool,
    usage: Usage | None,
    latency_ms: float,
    log_mode: str,
) -> RequestRecord:
    return RequestRecord(
        id=str(uuid4()),
        created_at=datetime.now(UTC).isoformat(),
        tenant_hash=tenant_hash,
        session_id=session_id[:128] if session_id is not None else None,
        upstream=upstream,
        wire_format=wire_format,
        cache_mode=cache_mode,
        model=model,
        stream=int(stream),
        upstream_status=upstream_status,
        complete=None if complete is None else int(complete),
        truncated=int(truncated),
        input_tokens=usage.input_tokens if usage is not None else None,
        output_tokens=usage.output_tokens if usage is not None else None,
        cache_write_tokens=usage.cache_write_tokens if usage is not None else None,
        cache_read_tokens=usage.cache_read_tokens if usage is not None else None,
        latency_ms=latency_ms,
        log_mode=log_mode,
    )


def build_segment_rows(request_id: str, request: CanonicalRequest) -> tuple[SegmentRow, ...]:
    """Hash every segment. Only hashes and metadata are stored, never segment content."""
    hashes = hash_segments(request)
    return tuple(
        SegmentRow(
            request_id=request_id,
            idx=segment.idx,
            kind=segment.kind.value,
            role=segment.role,
            raw_hash=digest.raw_hash,
            canon_hash=digest.canon_hash,
            byte_len=digest.byte_len,
            has_breakpoint=segment.has_breakpoint,
        )
        for segment, digest in zip(request.segments, hashes, strict=True)
    )


__all__ = ["build_record", "build_segment_rows", "tenant_hash_for"]
