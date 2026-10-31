import json
from pathlib import Path

import pytest

from pcg.adapters.openai_chat import OpenAIChatAdapter
from pcg.adapters.types import CacheMode, SegmentKind, Usage, UsageReporting
from pcg.config import WireFormat
from pcg.stream_tee import parse_openai_chat_stream
from tests.adapters.conformance import assert_adapter_conforms

pytestmark = pytest.mark.proxy
FIXTURES = Path(__file__).parent / "fixtures" / "openai_chat"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> object:
    return json.loads(fixture_bytes(name))


def parse(body: bytes):  # type: ignore[no-untyped-def]
    return OpenAIChatAdapter().parse_request(body, {})


def test_openai_chat_adapter_conforms() -> None:
    assert_adapter_conforms(
        OpenAIChatAdapter(),
        sample_raw_request=fixture_bytes("request_plain.json"),
        sample_headers={"authorization": "Bearer test-client-key"},
        plain_response=fixture_json("response_plain.json"),
        tool_call_response=fixture_json("response_tool_calls.json"),
    )


def test_identity_and_cache_profile() -> None:
    adapter = OpenAIChatAdapter()
    profile = adapter.cache_profile()

    assert adapter.wire_format is WireFormat.OPENAI_CHAT
    assert adapter.ingress_routes() == ["/v1/chat/completions"]
    assert adapter.auth_header_names() == ["authorization"]
    assert profile.cache_mode is CacheMode.AUTO_PREFIX
    assert profile.usage_reporting is UsageReporting.READ_ONLY
    assert profile.min_cacheable_tokens("any-model") == 1024


def test_tools_then_messages_in_wire_order_with_system_kinds() -> None:
    request = parse(fixture_bytes("request_with_tools.json"))

    assert [(s.kind, s.role) for s in request.segments] == [
        (SegmentKind.TOOL, None),
        (SegmentKind.TOOL, None),
        (SegmentKind.SYSTEM, "developer"),
        (SegmentKind.MESSAGE, "user"),
    ]
    assert [s.idx for s in request.segments] == [0, 1, 2, 3]
    assert not any(s.has_breakpoint for s in request.segments)


def test_system_message_after_user_message_keeps_wire_position() -> None:
    body = (
        b'{"model":"m","messages":[{"role":"user","content":"a"},'
        b'{"role":"system","content":"late"},{"role":"user","content":"b"}]}'
    )

    assert [s.kind for s in parse(body).segments] == [
        SegmentKind.MESSAGE,
        SegmentKind.SYSTEM,
        SegmentKind.MESSAGE,
    ]


def test_segments_are_byte_exact() -> None:
    body = (
        b'{ "messages" : [ { "content" : "caf\\u00e9" ,\n "role":"user" } ] ,'
        b' "model":"m" , "prompt_cache_key":"k" }'
    )

    request = parse(body)

    assert request.segments[0].raw == b'{ "content" : "caf\\u00e9" ,\n "role":"user" }'
    assert request.params == {"prompt_cache_key": "k"}


def test_legacy_functions_are_tool_segments() -> None:
    body = b'{"model":"m","functions":[{"name":"f"}],"messages":[{"role":"user","content":"x"}]}'

    request = parse(body)

    assert request.segments[0].kind is SegmentKind.TOOL
    assert request.segments[0].raw == b'{"name":"f"}'


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b'{"messages":[]}',
        b'{"model":"m","stream":"yes","messages":[]}',
        b'{"model":"m","messages":{}}',
        b'{"model":"m","messages":["x"]}',
        b'{"model":"m","messages":[{"content":"no role"}]}',
        b'{"model":"m","tools":{}}',
        b"not json",
    ],
)
def test_invalid_requests_raise_value_error(body: bytes) -> None:
    with pytest.raises(ValueError):
        parse(body)


def test_usage_is_normalized_to_uncached_input() -> None:
    usage = OpenAIChatAdapter().extract_usage(fixture_json("response_plain.json"))

    # prompt_tokens 2006 includes the 1920 cached tokens (ADR-016).
    assert usage == Usage(
        input_tokens=86, output_tokens=300, cache_write_tokens=None, cache_read_tokens=1920
    )


def test_missing_details_means_cache_reads_unknown_not_zero() -> None:
    usage = OpenAIChatAdapter().extract_usage(
        {"usage": {"prompt_tokens": 50, "completion_tokens": 3}}
    )

    assert usage == Usage(50, 3, None, None)


def test_zero_cached_tokens_is_a_known_zero() -> None:
    usage = OpenAIChatAdapter().extract_usage(
        {
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 0},
            }
        }
    )

    assert usage.cache_read_tokens == 0


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"usage": None},
        {"usage": {"prompt_tokens": 5}},
        {"usage": {"prompt_tokens": "5", "completion_tokens": 1}},
        {"usage": {"prompt_tokens": True, "completion_tokens": 1}},
        {
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 1,
                "prompt_tokens_details": {"cached_tokens": 9},
            }
        },
    ],
)
def test_bad_usage_raises(response: object) -> None:
    with pytest.raises(ValueError):
        OpenAIChatAdapter().extract_usage(response)


def test_only_complete_text_responses_are_cacheable() -> None:
    adapter = OpenAIChatAdapter()
    plain = fixture_json("response_plain.json")
    assert isinstance(plain, dict)

    assert adapter.is_cacheable_response(plain) is True
    assert adapter.is_cacheable_response(fixture_json("response_tool_calls.json")) is False
    assert adapter.is_cacheable_response(fixture_json("response_error.json")) is False
    truncated = json.loads(json.dumps(plain))
    truncated["choices"][0]["finish_reason"] = "length"
    assert adapter.is_cacheable_response(truncated) is False
    two = json.loads(json.dumps(plain))
    two["choices"].append(two["choices"][0])
    assert adapter.is_cacheable_response(two) is False
    legacy = json.loads(json.dumps(plain))
    legacy["choices"][0]["message"]["function_call"] = {"name": "f", "arguments": "{}"}
    assert adapter.is_cacheable_response(legacy) is False


def test_replay_is_not_implemented_until_phase_4() -> None:
    adapter = OpenAIChatAdapter()

    with pytest.raises(NotImplementedError):
        adapter.render_response({})
    with pytest.raises(NotImplementedError):
        adapter.synthesize_sse({})


# --- stream parsing ----------------------------------------------------------


def test_stream_with_usage_chunk() -> None:
    summary = parse_openai_chat_stream(fixture_bytes("stream_plain.sse"))

    assert summary.complete is True
    assert summary.stop_reason == "stop"
    assert summary.usage == Usage(86, 5, None, 1920)


def test_stream_without_include_usage_has_unknown_usage() -> None:
    summary = parse_openai_chat_stream(fixture_bytes("stream_no_usage.sse"))

    assert summary.complete is True
    assert summary.usage is None


def test_truncated_stream_is_not_complete() -> None:
    summary = parse_openai_chat_stream(fixture_bytes("stream_truncated.sse"))

    assert summary.complete is False
    assert summary.stop_reason is None


def test_stream_parsing_tolerates_crlf_and_garbage_frames() -> None:
    stream = fixture_bytes("stream_plain.sse")

    assert parse_openai_chat_stream(stream.replace(b"\n", b"\r\n")) == parse_openai_chat_stream(
        stream
    )
    noisy = b"data: {not json\n\n: keepalive comment\n\n" + stream
    assert parse_openai_chat_stream(noisy) == parse_openai_chat_stream(stream)
