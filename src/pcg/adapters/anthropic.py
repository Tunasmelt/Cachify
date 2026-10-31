import re
from collections.abc import Mapping

from pcg.adapters import types as t
from pcg.adapters.base import ProviderAdapter
from pcg.config import WireFormat
from pcg.segmenter import array_element_spans, load_object, slice_bytes

_MODEL_MINIMUMS = (
    (("fable", "5", "1"), 512),
    (("mythos", "5", "1"), 512),
    (("opus", "5", "5"), 512),
    (("sonnet", "5", "5"), 512),
    (("opus", "4", "8"), 1024),
    (("opus", "4", "1"), 1024),
    (("sonnet", "4", "6"), 1024),
    (("sonnet", "4", "5"), 1024),
    (("mythos", "preview"), 2048),
    (("opus", "4", "7"), 2048),
    (("haiku", "3", "5"), 2048),
    (("3", "5", "haiku"), 2048),
    (("opus", "4", "6"), 4096),
    (("opus", "4", "5"), 4096),
    (("haiku", "4", "5"), 4096),
    (("fable", "5"), 512),
    (("mythos", "5"), 512),
    (("opus", "5"), 512),
    (("sonnet", "5"), 1024),
    (("opus", "4"), 1024),
    (("sonnet", "4"), 1024),
)


def min_cacheable_tokens(model: str) -> int:
    """Match family/version tokens anywhere in an Anthropic model ID, most specific first."""
    tokens = tuple(re.findall(r"[a-z]+|\d+", model.lower()))
    for pattern, minimum in _MODEL_MINIMUMS:
        width = len(pattern)
        indexes = range(len(tokens) - width + 1)
        if any(tokens[index : index + width] == pattern for index in indexes):
            return minimum

    # Unknown models use the largest known minimum to avoid reporting cacheability too early.
    return 4096


def _has_cache_control(value: object, *, inspect_content: bool = False) -> bool:
    if not isinstance(value, dict):
        return False
    if "cache_control" in value:
        return True
    content = value.get("content")
    return (
        inspect_content
        and isinstance(content, list)
        and any(isinstance(block, dict) and "cache_control" in block for block in content)
    )


def _required_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise ValueError(f"usage.{field} must be an integer")
    return value


class AnthropicAdapter(ProviderAdapter):
    wire_format = WireFormat.ANTHROPIC_MESSAGES

    def ingress_routes(self) -> list[str]:
        return ["/v1/messages"]

    def auth_header_names(self) -> list[str]:
        return ["x-api-key"]

    def parse_request(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> t.CanonicalRequest:
        del headers
        data, text, spans = load_object(raw_body)

        model = data.get("model")
        if not isinstance(model, str) or not model:
            raise ValueError("model must be a non-empty string")
        stream = data.get("stream", False)
        if not isinstance(stream, bool):
            raise ValueError("stream must be a boolean")

        # (kind, role, parsed value for inspection, exact bytes as sent)
        parts: list[tuple[t.SegmentKind, str | None, object, bytes]] = []
        tools = data.get("tools", [])
        if not isinstance(tools, list):
            raise ValueError("tools must be a list")
        if tools:
            tool_spans = array_element_spans(text, spans["tools"])
            parts.extend(
                (t.SegmentKind.TOOL, None, tool, slice_bytes(text, span))
                for tool, span in zip(tools, tool_spans, strict=True)
            )

        system = data.get("system")
        if isinstance(system, str):
            parts.append((t.SegmentKind.SYSTEM, None, system, slice_bytes(text, spans["system"])))
        elif isinstance(system, list):
            system_spans = array_element_spans(text, spans["system"])
            parts.extend(
                (t.SegmentKind.SYSTEM, None, block, slice_bytes(text, span))
                for block, span in zip(system, system_spans, strict=True)
            )
        elif system is not None:
            raise ValueError("system must be a string or a list")

        messages = data.get("messages", [])
        if not isinstance(messages, list):
            raise ValueError("messages must be a list")
        message_spans = array_element_spans(text, spans["messages"]) if messages else []
        for message, span in zip(messages, message_spans, strict=True):
            if not isinstance(message, dict):
                raise ValueError("each message must be an object")
            role = message.get("role")
            if not isinstance(role, str):
                raise ValueError("message role must be a string")
            parts.append((t.SegmentKind.MESSAGE, role, message, slice_bytes(text, span)))

        segments = tuple(
            t.Segment(
                kind,
                index,
                role,
                raw,
                _has_cache_control(value, inspect_content=kind is t.SegmentKind.MESSAGE),
            )
            for index, (kind, role, value, raw) in enumerate(parts)
        )
        excluded = {"model", "stream", "tools", "system", "messages"}
        params = {key: value for key, value in data.items() if key not in excluded}
        return t.CanonicalRequest(model, stream, segments, params)

    def cache_profile(self) -> t.CacheProfile:
        return t.CacheProfile(
            cache_mode=t.CacheMode.EXPLICIT_BREAKPOINT,
            min_cacheable_tokens=min_cacheable_tokens,
            default_ttl_s=300,
            usage_reporting=t.UsageReporting.FULL,
            segment_order=(
                t.SegmentKind.TOOL,
                t.SegmentKind.SYSTEM,
                t.SegmentKind.MESSAGE,
            ),
        )

    def is_cacheable_response(self, response: object) -> bool:
        if not isinstance(response, dict):
            return False
        content = response.get("content")
        return (
            response.get("type") == "message"
            and response.get("stop_reason") == "end_turn"
            and isinstance(content, list)
            and not any(
                isinstance(block, dict) and block.get("type") == "tool_use" for block in content
            )
        )

    def extract_usage(self, response: object) -> t.Usage:
        if not isinstance(response, dict):
            raise ValueError("successful message is missing usage")
        usage = response.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("successful message is missing usage")
        try:
            input_tokens = _required_int(usage["input_tokens"], "input_tokens")
            output_tokens = _required_int(usage["output_tokens"], "output_tokens")
        except KeyError as error:
            raise ValueError("successful message usage is missing required token counts") from error

        cache_write = (
            _required_int(usage["cache_creation_input_tokens"], "cache_creation_input_tokens")
            if "cache_creation_input_tokens" in usage
            else None
        )
        cache_read = (
            _required_int(usage["cache_read_input_tokens"], "cache_read_input_tokens")
            if "cache_read_input_tokens" in usage
            else None
        )
        return t.Usage(input_tokens, output_tokens, cache_write, cache_read)

    def render_response(self, entry: object) -> object:
        del entry
        raise NotImplementedError("replay lands in Phase 4 (M4.4)")

    def synthesize_sse(self, entry: object) -> object:
        del entry
        raise NotImplementedError("replay lands in Phase 4 (M4.4)")
