"""Byte-exact JSON span extraction (ADR-006).

Cache providers match on the bytes a client sent, so the raw hash must cover the exact
text of each element, not a re-serialization of the parsed value. These helpers locate
the original byte span of top-level values and array elements without altering them.
"""

import json

_DECODER = json.JSONDecoder()
_WHITESPACE = " \t\r\n"

Span = tuple[int, int]


def decode_body(raw_body: bytes) -> str:
    try:
        return raw_body.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("request body must be UTF-8") from error


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index] in _WHITESPACE:
        index += 1
    return index


def _expect(text: str, index: int, char: str) -> int:
    if index >= len(text) or text[index] != char:
        raise ValueError(f"expected {char!r} at offset {index}")
    return index + 1


def top_level_spans(text: str) -> dict[str, Span]:
    """Map each key of a top-level JSON object to the span of its value.

    A repeated key keeps the last span, matching ``json.loads``.
    """
    spans: dict[str, Span] = {}
    index = _skip_whitespace(text, 0)
    index = _expect(text, index, "{")
    index = _skip_whitespace(text, index)
    if index < len(text) and text[index] == "}":
        return spans
    while True:
        index = _skip_whitespace(text, index)
        key, index = _DECODER.raw_decode(text, index)
        if not isinstance(key, str):
            raise ValueError("object key must be a string")
        index = _skip_whitespace(text, index)
        index = _expect(text, index, ":")
        start = _skip_whitespace(text, index)
        _, end = _DECODER.raw_decode(text, start)
        spans[key] = (start, end)
        index = _skip_whitespace(text, end)
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        _expect(text, index, "}")
        return spans


def array_element_spans(text: str, span: Span) -> list[Span]:
    """Return the span of every element of the JSON array occupying ``span``."""
    start, end = span
    index = _expect(text, start, "[")
    elements: list[Span] = []
    index = _skip_whitespace(text, index)
    if index < end and text[index] == "]":
        return elements
    while True:
        index = _skip_whitespace(text, index)
        _, element_end = _DECODER.raw_decode(text, index)
        elements.append((index, element_end))
        index = _skip_whitespace(text, element_end)
        if index < end and text[index] == ",":
            index += 1
            continue
        _expect(text, index, "]")
        return elements


def slice_bytes(text: str, span: Span) -> bytes:
    return text[span[0] : span[1]].encode("utf-8")


__all__ = ["Span", "array_element_spans", "decode_body", "slice_bytes", "top_level_spans"]
