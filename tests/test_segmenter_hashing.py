import pytest

from pcg.adapters.anthropic import AnthropicAdapter
from pcg.adapters.types import CanonicalRequest, SegmentKind
from pcg.hashing import SegmentHashes, hash_segment, hash_segments
from pcg.segmenter import array_element_spans, top_level_spans

pytestmark = pytest.mark.detector


def parse(body: bytes) -> CanonicalRequest:
    return AnthropicAdapter().parse_request(body, {})


def first_divergence(a: CanonicalRequest, b: CanonicalRequest, attr: str) -> int | None:
    ha, hb = hash_segments(a), hash_segments(b)
    for index, (x, y) in enumerate(zip(ha, hb, strict=False)):
        if getattr(x, attr) != getattr(y, attr):
            return index
    return None if len(ha) == len(hb) else min(len(ha), len(hb))


BASE = (
    b'{"model":"claude-sonnet-4-5-test","max_tokens":8,'
    b'"tools":[{"name":"a","description":"A"},{"name":"b","description":"B"}],'
    b'"system":"You are helpful.",'
    b'"messages":[{"role":"user","content":"one"},{"role":"assistant","content":"two"},'
    b'{"role":"user","content":"three"}]}'
)


def test_segments_are_ordered_and_byte_exact() -> None:
    request = parse(BASE)

    assert [s.kind for s in request.segments] == [
        SegmentKind.TOOL,
        SegmentKind.TOOL,
        SegmentKind.SYSTEM,
        SegmentKind.MESSAGE,
        SegmentKind.MESSAGE,
        SegmentKind.MESSAGE,
    ]
    assert [s.idx for s in request.segments] == list(range(6))
    assert request.segments[0].raw == b'{"name":"a","description":"A"}'
    assert request.segments[2].raw == b'"You are helpful."'
    assert request.segments[3].role == "user"
    assert request.segments[4].raw == b'{"role":"assistant","content":"two"}'


def test_original_whitespace_key_order_and_escapes_are_preserved() -> None:
    body = (
        b'{ "messages" : [ { "content" : "caf\\u00e9 \\/ x" ,\n "role":"user" } ] ,'
        b' "model":"claude-sonnet-4-5-test" }'
    )

    segment = parse(body).segments[0]

    assert segment.raw == b'{ "content" : "caf\\u00e9 \\/ x" ,\n "role":"user" }'


def test_non_ascii_text_round_trips_exactly() -> None:
    body = '{"model":"m","messages":[{"role":"user","content":"héllo 😀"}]}'.encode()

    assert parse(body).segments[0].raw == '{"role":"user","content":"héllo 😀"}'.encode()


def test_system_as_blocks_and_breakpoints() -> None:
    body = (
        b'{"model":"m","system":[{"type":"text","text":"a"},'
        b'{"type":"text","text":"b","cache_control":{"type":"ephemeral"}}],'
        b'"messages":[{"role":"user","content":[{"type":"text","text":"q",'
        b'"cache_control":{"type":"ephemeral"}}]}]}'
    )

    segments = parse(body).segments

    assert [s.kind for s in segments] == [
        SegmentKind.SYSTEM,
        SegmentKind.SYSTEM,
        SegmentKind.MESSAGE,
    ]
    assert [s.has_breakpoint for s in segments] == [False, True, True]


def test_segment_order_follows_cache_order_not_key_order() -> None:
    body = (
        b'{"messages":[{"role":"user","content":"q"}],"system":"s",'
        b'"tools":[{"name":"t"}],"model":"m"}'
    )

    assert [s.kind for s in parse(body).segments] == [
        SegmentKind.TOOL,
        SegmentKind.SYSTEM,
        SegmentKind.MESSAGE,
    ]


def test_empty_and_missing_sections_yield_no_segments() -> None:
    assert parse(b'{"model":"m","tools":[],"messages":[]}').segments == ()
    assert parse(b'{"model":"m"}').segments == ()


def test_duplicate_top_level_key_uses_last_value_like_json_loads() -> None:
    body = b'{"model":"m","system":"first","system":"second"}'

    assert parse(body).segments[0].raw == b'"second"'


def test_utf8_bom_is_accepted_and_not_part_of_segments() -> None:
    body = b"\xef\xbb\xbf" + b'{"model":"m","messages":[{"role":"user","content":"x"}]}'

    assert parse(body).segments[0].raw == b'{"role":"user","content":"x"}'


def test_non_utf8_body_is_rejected() -> None:
    body = '{"model":"m"}'.encode("utf-16")

    with pytest.raises(ValueError):
        parse(body)


def test_span_helpers_handle_empty_containers() -> None:
    assert top_level_spans("{}") == {}
    text = "[ ]"
    assert array_element_spans(text, (0, len(text))) == []


# --- hashing -----------------------------------------------------------------


def test_hash_segment_known_vector() -> None:
    # raw hashes the bytes as given; canon hashes sorted compact JSON '{"a":2,"b":1}'.
    assert hash_segment(b'{"b":1,"a":2}') == SegmentHashes(
        raw_hash="a1d46c3cdb4e5795c8d637f80daeb578ebb1a9a65dc1ed5f11f51794c3c89f3a",
        canon_hash="d3626ac30a87e6f7a6428233b3c68299976865fa5508e4267c5415c76af7a772",
        byte_len=13,
    )


def test_identical_requests_hash_identically() -> None:
    assert hash_segments(parse(BASE)) == hash_segments(parse(BASE))
    assert first_divergence(parse(BASE), parse(BASE), "raw_hash") is None


def test_key_order_only_change_differs_raw_but_not_canon() -> None:
    reordered = BASE.replace(
        b'{"role":"assistant","content":"two"}', b'{"content":"two","role":"assistant"}'
    )
    a, b = parse(BASE), parse(reordered)

    assert first_divergence(a, b, "raw_hash") == 4
    assert first_divergence(a, b, "canon_hash") is None


def test_whitespace_only_change_differs_raw_but_not_canon() -> None:
    spaced = BASE.replace(b'{"name":"a","description":"A"}', b'{ "name": "a", "description": "A" }')
    a, b = parse(BASE), parse(spaced)

    assert first_divergence(a, b, "raw_hash") == 0
    assert first_divergence(a, b, "canon_hash") is None


def test_unicode_escape_vs_literal_differs_raw_but_not_canon() -> None:
    literal = b'{"model":"m","system":"caf\xc3\xa9"}'
    escaped = b'{"model":"m","system":"caf\\u00e9"}'
    a, b = parse(literal), parse(escaped)

    assert first_divergence(a, b, "raw_hash") == 0
    assert first_divergence(a, b, "canon_hash") is None


def test_tool_reorder_changes_both_hashes_at_first_tool() -> None:
    swapped = BASE.replace(
        b'[{"name":"a","description":"A"},{"name":"b","description":"B"}]',
        b'[{"name":"b","description":"B"},{"name":"a","description":"A"}]',
    )
    a, b = parse(BASE), parse(swapped)

    assert first_divergence(a, b, "raw_hash") == 0
    assert first_divergence(a, b, "canon_hash") == 0


def test_timestamp_in_system_changes_both_hashes_at_system_only() -> None:
    stamped = BASE.replace(b"You are helpful.", b"You are helpful. Now 2026-10-24T10:00:00Z")
    a, b = parse(BASE), parse(stamped)

    assert first_divergence(a, b, "raw_hash") == 2
    assert first_divergence(a, b, "canon_hash") == 2


def test_edited_earlier_message_diverges_at_that_message() -> None:
    edited = BASE.replace(b'"content":"one"', b'"content":"ONE"')
    a, b = parse(BASE), parse(edited)

    assert first_divergence(a, b, "raw_hash") == 3
    assert first_divergence(a, b, "canon_hash") == 3


def test_appended_message_keeps_prefix_hashes() -> None:
    longer = BASE.replace(
        b'{"role":"user","content":"three"}]',
        b'{"role":"user","content":"three"},{"role":"assistant","content":"four"}]',
    )
    a, b = parse(BASE), parse(longer)

    assert hash_segments(b)[: len(a.segments)] == hash_segments(a)
    assert first_divergence(a, b, "raw_hash") == len(a.segments)
