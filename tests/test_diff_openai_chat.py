"""Gate G2 golden request pairs for the openai-chat wire format (mirrors test_diff.py)."""

import pytest

from pcg.adapters.openai_chat import OpenAIChatAdapter
from pcg.diff import Divergence, DivergenceKind, first_divergence
from pcg.hashing import hash_segments

pytestmark = pytest.mark.detector

BASE = (
    b'{"model":"gpt-test",'
    b'"tools":[{"type":"function","function":{"name":"a"}},{"type":"function","function":{"name":"b"}}],'
    b'"messages":[{"role":"system","content":"You are helpful."},'
    b'{"role":"user","content":"one"},{"role":"assistant","content":"two"},'
    b'{"role":"user","content":"three"}]}'
)


def diff(previous: bytes, current: bytes) -> Divergence:
    adapter = OpenAIChatAdapter()

    def pairs(body: bytes) -> list[tuple[str, str]]:
        digests = hash_segments(adapter.parse_request(body, {}))
        return [(d.raw_hash, d.canon_hash) for d in digests]

    return first_divergence(pairs(current), pairs(previous))


def test_identical_requests_have_no_divergence() -> None:
    assert diff(BASE, BASE) == Divergence(None, DivergenceKind.NONE)


def test_tool_reorder_diverges_at_first_tool_as_content() -> None:
    swapped = BASE.replace(
        b'{"type":"function","function":{"name":"a"}},{"type":"function","function":{"name":"b"}}',
        b'{"type":"function","function":{"name":"b"}},{"type":"function","function":{"name":"a"}}',
    )

    assert diff(BASE, swapped) == Divergence(0, DivergenceKind.CONTENT)


def test_timestamp_in_system_message_diverges_at_system_segment() -> None:
    stamped = BASE.replace(b"You are helpful.", b"You are helpful. Now 2026-10-24T10:00:00Z")

    assert diff(BASE, stamped) == Divergence(2, DivergenceKind.CONTENT)


def test_edited_earlier_message_diverges_at_that_message() -> None:
    edited = BASE.replace(b'"content":"two"', b'"content":"TWO"')

    assert diff(BASE, edited) == Divergence(4, DivergenceKind.CONTENT)


def test_key_order_only_change_is_serialization() -> None:
    reordered = BASE.replace(
        b'{"role":"assistant","content":"two"}', b'{"content":"two","role":"assistant"}'
    )

    assert diff(BASE, reordered) == Divergence(4, DivergenceKind.SERIALIZATION)


def test_whitespace_only_change_is_serialization() -> None:
    spaced = BASE.replace(
        b'{"role":"user","content":"one"}', b'{ "role": "user", "content": "one" }'
    )

    assert diff(BASE, spaced) == Divergence(3, DivergenceKind.SERIALIZATION)


def test_appended_turn_is_append_at_old_length() -> None:
    longer = BASE.replace(
        b'{"role":"user","content":"three"}]',
        b'{"role":"user","content":"three"},{"role":"assistant","content":"four"}]',
    )

    assert diff(BASE, longer) == Divergence(6, DivergenceKind.APPEND)
