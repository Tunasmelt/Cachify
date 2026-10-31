"""Per-wire-format gateway support: adapter, upstream path, headers, stream parsing.

The gateway looks up everything wire-specific here so it never hard-codes one provider.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pcg.adapters.anthropic import AnthropicAdapter
from pcg.adapters.base import ProviderAdapter
from pcg.adapters.openai_chat import OpenAIChatAdapter
from pcg.config import WireFormat
from pcg.stream_tee import StreamSummary, parse_anthropic_stream, parse_openai_chat_stream


@dataclass(frozen=True)
class WireSupport:
    adapter: ProviderAdapter
    upstream_path: str
    # Client headers forwarded upstream besides the adapter's auth headers.
    extra_headers: tuple[str, ...]
    client_key: Callable[[Mapping[str, str]], str]
    parse_stream: Callable[[bytes], StreamSummary]


# These indirections resolve the parser at call time, so tests can patch the parser module
# attribute and so a failing parser is caught by the gateway's guard at the call site.
def _parse_anthropic(data: bytes) -> StreamSummary:
    return parse_anthropic_stream(data)


def _parse_openai_chat(data: bytes) -> StreamSummary:
    return parse_openai_chat_stream(data)


def _anthropic_key(headers: Mapping[str, str]) -> str:
    return headers.get("x-api-key", "")


def _bearer_key(headers: Mapping[str, str]) -> str:
    value = headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token else value.strip()


_SUPPORT: dict[WireFormat, WireSupport] = {
    WireFormat.ANTHROPIC_MESSAGES: WireSupport(
        adapter=AnthropicAdapter(),
        upstream_path="/v1/messages",
        extra_headers=("content-type", "anthropic-version"),
        client_key=_anthropic_key,
        parse_stream=_parse_anthropic,
    ),
    WireFormat.OPENAI_CHAT: WireSupport(
        adapter=OpenAIChatAdapter(),
        upstream_path="/v1/chat/completions",
        extra_headers=("content-type", "openai-organization", "openai-project"),
        client_key=_bearer_key,
        parse_stream=_parse_openai_chat,
    ),
}


def wire_support(wire_format: WireFormat) -> WireSupport | None:
    return _SUPPORT.get(wire_format)


__all__ = ["WireSupport", "wire_support"]
