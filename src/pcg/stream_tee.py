import json
import re
from dataclasses import dataclass

from pcg.adapters.anthropic import AnthropicAdapter
from pcg.adapters.openai_chat import OpenAIChatAdapter
from pcg.adapters.types import Usage


@dataclass(frozen=True)
class StreamSummary:
    usage: Usage | None
    stop_reason: str | None
    complete: bool


AnthropicStreamSummary = StreamSummary


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


def parse_anthropic_stream(data: bytes) -> StreamSummary:
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

    return StreamSummary(usage, stop_reason, complete)


def parse_openai_chat_stream(data: bytes) -> StreamSummary:
    """Summarize an OpenAI Chat Completions SSE stream (``data:`` chunks, then ``[DONE]``).

    Usage only appears when the client set ``stream_options.include_usage``; otherwise it
    stays ``None`` (unknown, not zero).
    """
    adapter = OpenAIChatAdapter()
    usage: Usage | None = None
    stop_reason: str | None = None
    complete = False

    for frame in re.split(rb"\r?\n\r?\n", data):
        data_lines = [
            value
            for line in frame.splitlines()
            if (value := _line_value(line, b"data:")) is not None
        ]
        if not data_lines:
            continue
        if b"\n".join(data_lines).strip() == b"[DONE]":
            complete = True
            continue
        payload = _decode_data(data_lines)
        if not isinstance(payload, dict):
            continue

        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            finish_reason = choices[0].get("finish_reason")
            if isinstance(finish_reason, str):
                stop_reason = finish_reason
        chunk_usage = payload.get("usage")
        if isinstance(chunk_usage, dict):
            try:
                usage = adapter.extract_usage({"usage": chunk_usage})
            except ValueError:
                usage = None

    return StreamSummary(usage, stop_reason, complete)


__all__ = [
    "AnthropicStreamSummary",
    "StreamSummary",
    "StreamTee",
    "parse_anthropic_stream",
    "parse_openai_chat_stream",
]
