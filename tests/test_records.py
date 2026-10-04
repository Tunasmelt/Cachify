import hashlib

import pytest

from pcg.adapters.types import Usage
from pcg.records import build_record, tenant_hash_for

pytestmark = pytest.mark.proxy


def record_for(usage: Usage | None, session_id: str | None = None) -> object:
    return build_record(
        tenant_hash="tenant",
        session_id=session_id,
        upstream="primary",
        wire_format="anthropic-messages",
        cache_mode="explicit_breakpoint",
        model="model",
        stream=False,
        upstream_status=200,
        complete=None,
        truncated=False,
        usage=usage,
        latency_ms=1.5,
        log_mode="hash",
    )


def test_tenant_hash_is_salted_hmac() -> None:
    key = "test-client-key"
    first = tenant_hash_for(b"x" * 32, key)

    assert first == tenant_hash_for(b"x" * 32, key)
    assert first != hashlib.sha256(key.encode()).hexdigest()
    assert first != tenant_hash_for(b"y" * 32, key)


def test_build_record_without_usage_keeps_tokens_null() -> None:
    record = record_for(None)

    assert record.input_tokens is None
    assert record.output_tokens is None
    assert record.cache_write_tokens is None
    assert record.cache_read_tokens is None


def test_build_record_copies_usage_and_preserves_unknown_cache_fields() -> None:
    record = record_for(Usage(10, 4, None, 3))

    assert record.input_tokens == 10
    assert record.output_tokens == 4
    assert record.cache_write_tokens is None
    assert record.cache_read_tokens == 3


def test_build_record_truncates_session_id() -> None:
    record = record_for(None, "s" * 200)

    assert record.session_id == "s" * 128
