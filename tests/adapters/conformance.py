from pcg.adapters.base import ProviderAdapter
from pcg.adapters.types import (
    CacheMode,
    CacheProfile,
    CanonicalRequest,
    SegmentKind,
    Usage,
    UsageReporting,
)
from pcg.config import WireFormat


def assert_adapter_conforms(
    adapter: ProviderAdapter,
    *,
    sample_raw_request: bytes,
    sample_headers: dict[str, str],
    plain_response: object,
    tool_call_response: object,
) -> None:
    assert isinstance(adapter.wire_format, WireFormat)

    routes = adapter.ingress_routes()
    assert isinstance(routes, list) and routes
    assert all(isinstance(route, str) and route for route in routes)

    auth_headers = adapter.auth_header_names()
    assert isinstance(auth_headers, list) and auth_headers
    assert all(isinstance(header, str) and header for header in auth_headers)

    profile = adapter.cache_profile()
    assert isinstance(profile, CacheProfile)
    assert isinstance(profile.usage_reporting, UsageReporting)
    assert isinstance(profile.cache_mode, CacheMode)
    minimum = profile.min_cacheable_tokens("any-model")
    assert type(minimum) is int and minimum >= 0
    assert isinstance(profile.segment_order, tuple) and profile.segment_order
    assert all(isinstance(kind, SegmentKind) for kind in profile.segment_order)

    request = adapter.parse_request(sample_raw_request, sample_headers)
    assert isinstance(request, CanonicalRequest)
    assert isinstance(request.model, str) and request.model
    assert isinstance(request.stream, bool)
    assert all(segment.kind in profile.segment_order for segment in request.segments)

    usage = adapter.extract_usage(plain_response)
    assert isinstance(usage, Usage)
    assert type(usage.input_tokens) is int and usage.input_tokens >= 0
    assert type(usage.output_tokens) is int and usage.output_tokens >= 0

    assert adapter.is_cacheable_response(plain_response) is True
    assert adapter.is_cacheable_response(tool_call_response) is False
