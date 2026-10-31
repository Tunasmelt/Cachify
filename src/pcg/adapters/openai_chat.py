"""OpenAI Chat Completions wire format (also the shape most OpenAI-compatible providers use).

Caching here is automatic and prefix-based: there are no breakpoints, the usage block only
reports cached tokens read, and OpenAI counts those inside ``prompt_tokens``. Canonical
``Usage.input_tokens`` means uncached input (ADR-016), so cached tokens are subtracted.
"""

from collections.abc import Mapping

from pcg.adapters import types as t
from pcg.adapters.base import ProviderAdapter
from pcg.config import WireFormat
from pcg.segmenter import array_element_spans, load_object, slice_bytes

# Documented minimum for current models; earlier models vary (docs/API.md).
_MIN_CACHEABLE_TOKENS = 1024
# Unverified placeholder: OpenAI's retention window for Chat Completions is not yet confirmed.
_DEFAULT_TTL_S = 300
_SYSTEM_ROLES = frozenset({"system", "developer"})


def min_cacheable_tokens(model: str) -> int:
    del model
    return _MIN_CACHEABLE_TOKENS


def _required_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise ValueError(f"usage.{field} must be an integer")
    return value


class OpenAIChatAdapter(ProviderAdapter):
    wire_format = WireFormat.OPENAI_CHAT

    def ingress_routes(self) -> list[str]:
        return ["/v1/chat/completions"]

    def auth_header_names(self) -> list[str]:
        return ["authorization"]

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

        # Wire prefix order is tools (and legacy functions), then messages as sent.
        parts: list[tuple[t.SegmentKind, str | None, bytes]] = []
        for key in ("tools", "functions"):
            items = data.get(key, [])
            if not isinstance(items, list):
                raise ValueError(f"{key} must be a list")
            if items:
                item_spans = array_element_spans(text, spans[key])
                parts.extend(
                    (t.SegmentKind.TOOL, None, slice_bytes(text, span)) for span in item_spans
                )

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
            kind = t.SegmentKind.SYSTEM if role in _SYSTEM_ROLES else t.SegmentKind.MESSAGE
            parts.append((kind, role, slice_bytes(text, span)))

        segments = tuple(
            t.Segment(kind, index, role, raw, False)
            for index, (kind, role, raw) in enumerate(parts)
        )
        excluded = {"model", "stream", "tools", "functions", "messages"}
        params = {key: value for key, value in data.items() if key not in excluded}
        return t.CanonicalRequest(model, stream, segments, params)

    def cache_profile(self) -> t.CacheProfile:
        return t.CacheProfile(
            cache_mode=t.CacheMode.AUTO_PREFIX,
            min_cacheable_tokens=min_cacheable_tokens,
            default_ttl_s=_DEFAULT_TTL_S,
            usage_reporting=t.UsageReporting.READ_ONLY,
            segment_order=(
                t.SegmentKind.TOOL,
                t.SegmentKind.SYSTEM,
                t.SegmentKind.MESSAGE,
            ),
        )

    def is_cacheable_response(self, response: object) -> bool:
        if not isinstance(response, dict) or response.get("object") != "chat.completion":
            return False
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            return False
        choice = choices[0]
        if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
            return False
        message = choice.get("message")
        return (
            isinstance(message, dict)
            and not message.get("tool_calls")
            and not message.get("function_call")
        )

    def extract_usage(self, response: object) -> t.Usage:
        if not isinstance(response, dict):
            raise ValueError("successful completion is missing usage")
        usage = response.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("successful completion is missing usage")
        try:
            prompt_tokens = _required_int(usage["prompt_tokens"], "prompt_tokens")
            output_tokens = _required_int(usage["completion_tokens"], "completion_tokens")
        except KeyError as error:
            raise ValueError("successful completion usage is missing required counts") from error

        cached: int | None = None
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict) and "cached_tokens" in details:
            cached = _required_int(details["cached_tokens"], "cached_tokens")
            if cached > prompt_tokens:
                raise ValueError("usage.cached_tokens exceeds prompt_tokens")

        input_tokens = prompt_tokens - (cached or 0)
        # Unknown is not zero: no details block means cache reads were not reported.
        # OpenAI Chat Completions does not report cache writes at all.
        return t.Usage(input_tokens, output_tokens, None, cached)

    def render_response(self, entry: object) -> object:
        del entry
        raise NotImplementedError("replay lands in Phase 4 (M4.4)")

    def synthesize_sse(self, entry: object) -> object:
        del entry
        raise NotImplementedError("replay lands in Phase 4 (M4.4)")
