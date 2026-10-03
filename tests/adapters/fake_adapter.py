import json
from collections.abc import Mapping

from pcg.adapters import types as t
from pcg.adapters.base import ProviderAdapter
from pcg.config import WireFormat


class FakeAdapter(ProviderAdapter):
    wire_format = WireFormat.ANTHROPIC_MESSAGES

    def ingress_routes(self) -> list[str]:
        return ["/v1/fake"]

    def auth_header_names(self) -> list[str]:
        return ["x-api-key"]

    def parse_request(self, raw_body: bytes, headers: Mapping[str, str]) -> t.CanonicalRequest:
        del headers
        data = json.loads(raw_body)
        parts = [(t.SegmentKind.TOOL, None, value) for value in data["tools"]]
        if data["system"] is not None:
            parts.append((t.SegmentKind.SYSTEM, None, data["system"]))
        parts.extend(
            (t.SegmentKind.MESSAGE, value.get("role"), value) for value in data["messages"]
        )
        segments = tuple(
            t.Segment(kind, idx, role, json.dumps(value).encode(), False)
            for idx, (kind, role, value) in enumerate(parts)
        )
        return t.CanonicalRequest(data["model"], data["stream"], segments, {})

    def cache_profile(self) -> t.CacheProfile:
        order = (t.SegmentKind.TOOL, t.SegmentKind.SYSTEM, t.SegmentKind.MESSAGE)
        return t.CacheProfile(
            t.CacheMode.EXPLICIT_BREAKPOINT,
            lambda model: 0,
            300,
            t.UsageReporting.FULL,
            order,
        )

    def is_cacheable_response(self, response: object) -> bool:
        return isinstance(response, dict) and not response.get("tool_call")

    def extract_usage(self, response: object) -> t.Usage:
        assert isinstance(response, dict)
        return t.Usage(response["input_tokens"], response["output_tokens"], None, None)

    def render_response(self, entry: object) -> object:
        return entry

    def synthesize_sse(self, entry: object) -> object:
        return entry
