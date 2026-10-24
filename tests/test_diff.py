from collections.abc import Iterator
from pathlib import Path

import pytest

from pcg.adapters.anthropic import AnthropicAdapter
from pcg.diff import Divergence, DivergenceKind, first_divergence, resolve_divergence
from pcg.hashing import hash_segments
from pcg.store import RequestStore, SegmentRow
from tests.test_lineage import add, record, rows

pytestmark = pytest.mark.detector

BASE = (
    b'{"model":"claude-sonnet-4-5-test","max_tokens":8,'
    b'"tools":[{"name":"a","description":"A"},{"name":"b","description":"B"}],'
    b'"system":"You are helpful.",'
    b'"messages":[{"role":"user","content":"one"},{"role":"assistant","content":"two"},'
    b'{"role":"user","content":"three"}]}'
)


def diff(previous: bytes, current: bytes) -> Divergence:
    adapter = AnthropicAdapter()

    def pairs(body: bytes) -> list[tuple[str, str]]:
        digests = hash_segments(adapter.parse_request(body, {}))
        return [(d.raw_hash, d.canon_hash) for d in digests]

    return first_divergence(pairs(current), pairs(previous))


def test_identical_requests_have_no_divergence() -> None:
    assert diff(BASE, BASE) == Divergence(None, DivergenceKind.NONE)


def test_tool_reorder_diverges_at_first_tool_as_content() -> None:
    swapped = BASE.replace(
        b'[{"name":"a","description":"A"},{"name":"b","description":"B"}]',
        b'[{"name":"b","description":"B"},{"name":"a","description":"A"}]',
    )

    assert diff(BASE, swapped) == Divergence(0, DivergenceKind.CONTENT)


def test_timestamp_in_system_diverges_at_system_segment() -> None:
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
    spaced = BASE.replace(b'{"name":"a","description":"A"}', b'{ "name": "a", "description": "A" }')

    assert diff(BASE, spaced) == Divergence(0, DivergenceKind.SERIALIZATION)


def test_appended_turn_is_append_at_old_length() -> None:
    longer = BASE.replace(
        b'{"role":"user","content":"three"}]',
        b'{"role":"user","content":"three"},{"role":"assistant","content":"four"}]',
    )

    assert diff(BASE, longer) == Divergence(6, DivergenceKind.APPEND)


def test_trimmed_history_is_truncated_at_new_length() -> None:
    shorter = BASE.replace(b',{"role":"user","content":"three"}', b"")

    assert diff(BASE, shorter) == Divergence(5, DivergenceKind.TRUNCATED)


def test_earliest_difference_wins_over_later_ones() -> None:
    both = BASE.replace(b"You are helpful.", b"You are helpful!").replace(
        b'"content":"two"', b'"content":"TWO"'
    )

    assert diff(BASE, both) == Divergence(2, DivergenceKind.CONTENT)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RequestStore]:
    db = RequestStore(f"sqlite:///{(tmp_path / 'pcg.db').as_posix()}")
    db.migrate()
    yield db
    db.dispose()


def test_resolve_divergence_reads_predecessor_hashes(store: RequestStore) -> None:
    add(store, "prev", ["a", "b"])

    assert resolve_divergence(store, "prev", rows("new", ["a", "b", "c"])) == Divergence(
        2, DivergenceKind.APPEND
    )
    assert resolve_divergence(store, "prev", rows("new", ["a", "x"])) == Divergence(
        1, DivergenceKind.CONTENT
    )


def test_resolve_divergence_is_unknown_without_recorded_segments(store: RequestStore) -> None:
    store.insert(record("bare"), [])

    assert resolve_divergence(store, "bare", rows("new", ["a"])) is None
    add(store, "prev", ["a"])
    empty: list[SegmentRow] = []
    assert resolve_divergence(store, "prev", empty) is None
