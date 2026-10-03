import json
import re
from dataclasses import dataclass

from pcg.adapters.anthropic import AnthropicAdapter
from pcg.adapters.types import Usage


@dataclass(frozen=True)
class AnthropicStreamSummary:
    usage: Usage | None
    stop_reason: str | None
    complete: bool


class StreamTee:
    def __init__(self, max_buffer_bytes: int = 1_048_576) -> None:
        self._max_buffer_bytes = max_buffer_bytes
        self._buffer = bytearray()
        self._truncated = False
        self._failed = False

    @property
    def truncated(self) -> bool:
        return self._truncated

    @property
    def failed(self) -> bool:
        return self._failed

    def _append(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)

    def feed(self, chunk: bytes) -> None:
        try:
            if self._truncated or self._failed:
                return
            if len(self._buffer) + len(chunk) > self._max_buffer_bytes:
                self._truncated = True
                self._buffer.clear()
                return
            self._append(chunk)
        except Exception:
            self._failed = True

    def raw_events_bytes(self) -> bytes:
        if self._truncated:
            return b""
        return bytes(self._buffer)


def _line_value(line: bytes, prefix: bytes) -> bytes | None:
    if not line.startswith(prefix):
        return None
    return line[len(prefix) :].lstrip(b" ")


def _decode_data(data_lines: list[bytes]) -> object | None:
    try:
        decoded: object = json.loads(b"\n".join(data_lines))
        return decoded
    except Exception:
        return None


def parse_anthropic_stream(data: bytes) -> AnthropicStreamSummary:
    adapter = AnthropicAdapter()
    usage: Usage | None = None
    usage_data: dict[str, object] | None = None
    stop_reason: str | None = None
    complete = False

    for frame in re.split(rb"\r?\n\r?\n", data):
        event: bytes | None = None
        data_lines: list[bytes] = []
        for line in frame.splitlines():
            event_value = _line_value(line, b"event:")
            if event_value is not None:
                event = event_value
                continue
            data_value = _line_value(line, b"data:")
            if data_value is not None:
                data_lines.append(data_value)

        if event is None or not data_lines:
            continue
        payload = _decode_data(data_lines)
        if not isinstance(payload, dict):
            continue

        if event == b"message_start":
            message = payload.get("message")
            candidate = message.get("usage") if isinstance(message, dict) else None
            if not isinstance(candidate, dict):
                continue
            try:
                usage = adapter.extract_usage({"usage": candidate})
            except ValueError:
                usage = None
                usage_data = None
            else:
                usage_data = dict(candidate)
        elif event == b"message_delta":
            delta = payload.get("delta")
            candidate_stop_reason = delta.get("stop_reason") if isinstance(delta, dict) else None
            if isinstance(candidate_stop_reason, str):
                stop_reason = candidate_stop_reason

            delta_usage = payload.get("usage")
            if usage_data is None or not isinstance(delta_usage, dict):
                continue
            if "output_tokens" in delta_usage:
                updated_usage = {**usage_data, "output_tokens": delta_usage["output_tokens"]}
                try:
                    usage = adapter.extract_usage({"usage": updated_usage})
                except ValueError:
                    continue
                usage_data = updated_usage
        elif event == b"message_stop":
            complete = True

    return AnthropicStreamSummary(usage, stop_reason, complete)


__all__ = ["AnthropicStreamSummary", "StreamTee", "parse_anthropic_stream"]
