from pathlib import Path

import pytest

from pcg.stream_tee import StreamTee, parse_anthropic_stream

pytestmark = pytest.mark.proxy
FIXTURES = Path(__file__).parent / "fixtures" / "anthropic"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_buffer_cap_truncates_and_discards_buffer() -> None:
    tee = StreamTee(max_buffer_bytes=5)

    tee.feed(b"1234")
    tee.feed(b"56")

    assert tee.truncated is True
    assert tee.raw_events_bytes() == b""


def test_feed_after_truncation_is_ignored() -> None:
    tee = StreamTee(max_buffer_bytes=1)

    tee.feed(b"too large")
    tee.feed(b"x")

    assert tee.truncated is True
    assert tee.raw_events_bytes() == b""


def test_internal_feed_exception_sets_failed_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_internal_error(self: StreamTee, chunk: bytes) -> None:
        del self, chunk
        raise RuntimeError("synthetic tee failure")

    monkeypatch.setattr(StreamTee, "_append", raise_internal_error)
    tee = StreamTee()

    tee.feed(b"data")
    tee.feed(b"ignored")

    assert tee.failed is True


def test_parse_plain_stream_extracts_usage_and_completion() -> None:
    summary = parse_anthropic_stream(fixture_bytes("stream_plain.sse"))

    assert summary.usage is not None
    assert summary.usage.input_tokens == 42
    assert summary.usage.output_tokens == 7
    assert summary.usage.cache_write_tokens == 11
    assert summary.usage.cache_read_tokens == 23
    assert summary.stop_reason == "end_turn"
    assert summary.complete is True


def test_parse_truncated_stream_is_incomplete() -> None:
    summary = parse_anthropic_stream(fixture_bytes("stream_truncated.sse"))

    assert summary.complete is False


def test_parse_tool_use_stream_extracts_stop_reason() -> None:
    summary = parse_anthropic_stream(fixture_bytes("stream_tool_use.sse"))

    assert summary.stop_reason == "tool_use"
    assert summary.complete is True


def test_malformed_data_frame_is_skipped() -> None:
    malformed = b"event: message_delta\ndata: {not-json}\n\n"

    summary = parse_anthropic_stream(malformed + fixture_bytes("stream_plain.sse"))

    assert summary.stop_reason == "end_turn"
    assert summary.complete is True


def test_crlf_framing_matches_lf_framing() -> None:
    stream = fixture_bytes("stream_plain.sse")

    assert parse_anthropic_stream(stream.replace(b"\n", b"\r\n")) == parse_anthropic_stream(stream)


def test_missing_required_start_usage_does_not_invent_zeroes() -> None:
    stream = (
        b"event: message_start\n"
        b'data: {"message":{"usage":{"cache_read_input_tokens":2}}}\n\n'
        b"event: message_stop\n"
        b'data: {"type":"message_stop"}\n\n'
    )

    summary = parse_anthropic_stream(stream)

    assert summary.usage is None
    assert summary.complete is True
