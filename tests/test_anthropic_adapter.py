import json
from pathlib import Path

import pytest

from pcg.adapters.anthropic import AnthropicAdapter, min_cacheable_tokens
from pcg.adapters.types import SegmentKind, Usage
from tests.adapters.conformance import assert_adapter_conforms

pytestmark = pytest.mark.proxy
FIXTURES = Path(__file__).parent / "fixtures" / "anthropic"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> object:
    return json.loads(fixture_bytes(name))


def test_anthropic_adapter_conforms() -> None:
    assert_adapter_conforms(
        AnthropicAdapter(),
        sample_raw_request=fixture_bytes("request_plain.json"),
        sample_headers={"x-api-key": "test-client-key"},
        plain_response=fixture_json("response_plain.json"),
        tool_call_response=fixture_json("response_tool_use.json"),
    )


def test_non_complete_responses_are_not_cacheable() -> None:
    adapter = AnthropicAdapter()

    assert adapter.is_cacheable_response(fixture_json("response_error.json")) is False
    assert (
        adapter.is_cacheable_response(
            {"type": "message", "stop_reason": "max_tokens", "content": []}
        )
        is False
    )


def test_extract_usage_preserves_unknown_cache_fields() -> None:
    response = {
        "type": "message",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 9, "output_tokens": 3},
    }

    assert AnthropicAdapter().extract_usage(response) == Usage(9, 3, None, None)


def test_extract_usage_reads_reported_cache_fields() -> None:
    assert AnthropicAdapter().extract_usage(fixture_json("response_plain.json")) == Usage(
        20,
        2,
        10,
        5,
    )


def test_extract_usage_rejects_missing_usage() -> None:
    with pytest.raises(ValueError, match="missing usage"):
        AnthropicAdapter().extract_usage({"type": "message", "stop_reason": "end_turn"})


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-opus-4-5", 4096),
        ("claude-sonnet-4-5-20261003", 1024),
        ("claude-haiku-3-5", 2048),
        ("claude-3-5-haiku-20241022", 2048),
        ("claude-haiku-4-5-20251001", 4096),
        ("claude-fable-5-1", 512),
        ("claude-unknown-99", 4096),
    ],
)
def test_min_cacheable_tokens(model: str, expected: int) -> None:
    assert min_cacheable_tokens(model) == expected


def test_parse_request_orders_segments_and_finds_breakpoints() -> None:
    request = AnthropicAdapter().parse_request(
        fixture_bytes("request_with_cache.json"),
        {"x-api-key": "test-client-key"},
    )

    assert [segment.kind for segment in request.segments] == [
        SegmentKind.TOOL,
        SegmentKind.SYSTEM,
        SegmentKind.SYSTEM,
        SegmentKind.MESSAGE,
    ]
    assert [segment.has_breakpoint for segment in request.segments] == [False, True, False, True]


def test_replay_methods_are_deferred() -> None:
    adapter = AnthropicAdapter()

    with pytest.raises(NotImplementedError, match=r"Phase 4 \(M4\.4\)"):
        adapter.render_response({})
    with pytest.raises(NotImplementedError, match=r"Phase 4 \(M4\.4\)"):
        adapter.synthesize_sse({})
