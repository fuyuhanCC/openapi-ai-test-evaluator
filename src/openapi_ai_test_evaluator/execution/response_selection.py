"""Shared response value selection for assertions and extractions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from pydantic import JsonValue

from openapi_ai_test_evaluator.execution.response_parser import ResponseBodyKind
from openapi_ai_test_evaluator.execution.response_processor import ProcessedResponse
from openapi_ai_test_evaluator.execution.snapshots import is_sensitive_name

ResponseValueSource = Literal["response.body", "response.headers", "response.status"]


class ResponseSelectionError(ValueError):
    """A response value could not be selected deterministically."""


@dataclass(frozen=True, slots=True)
class SelectedResponseValue:
    """A selected JSON-compatible value, distinct from an absent value."""

    found: bool
    value: JsonValue | None


def select_response_value(
    response: ProcessedResponse,
    source: ResponseValueSource,
    pointer: str | None,
) -> SelectedResponseValue:
    """Select a response status, header, or body value with JSON Pointer semantics."""
    if source == "response.status":
        if pointer is not None:
            raise ResponseSelectionError("response.status cannot use a JSON Pointer")
        return SelectedResponseValue(found=True, value=response.raw.status_code)

    if pointer is None:
        raise ResponseSelectionError(f"{source} requires a JSON Pointer")
    if source == "response.headers":
        return _value_at_pointer(
            _headers_as_json(response.raw.headers),
            pointer,
            casefold_first_token=True,
        )

    if response.data is None:
        assert response.parse_issue is not None
        raise ResponseSelectionError(
            f"response body is unavailable: {response.parse_issue.message}"
        )
    if response.data.body_kind is ResponseBodyKind.EMPTY:
        return SelectedResponseValue(found=False, value=None)
    if response.data.body_kind is ResponseBodyKind.BINARY:
        raise ResponseSelectionError("binary response bodies cannot be selected")
    return _value_at_pointer(response.data.body, pointer)


def response_pointer_is_sensitive(source: ResponseValueSource, pointer: str | None) -> bool:
    """Return whether the selected field or header name is sensitive."""
    if source == "response.status" or not pointer:
        return False
    try:
        tokens = pointer_tokens(pointer)
    except ResponseSelectionError:
        return False
    return bool(tokens) and is_sensitive_name(tokens[-1])


def pointer_tokens(pointer: str) -> list[str]:
    """Decode an RFC 6901 JSON Pointer into reference tokens."""
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ResponseSelectionError("invalid JSON Pointer")
    return [_decode_pointer_token(token) for token in pointer[1:].split("/")]


def _headers_as_json(headers: tuple[tuple[str, str], ...]) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {}
    missing = object()
    for name, value in headers:
        key = name.casefold()
        existing = values.get(key, missing)
        if existing is missing:
            values[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            values[key] = [cast(JsonValue, existing), value]
    return values


def _value_at_pointer(
    value: JsonValue,
    pointer: str,
    *,
    casefold_first_token: bool = False,
) -> SelectedResponseValue:
    tokens = pointer_tokens(pointer)
    if casefold_first_token and tokens:
        tokens[0] = tokens[0].casefold()

    current: JsonValue = value
    for token in tokens:
        if isinstance(current, dict):
            if token not in current:
                return SelectedResponseValue(found=False, value=None)
            current = current[token]
            continue
        if isinstance(current, list):
            if not _is_array_index(token):
                return SelectedResponseValue(found=False, value=None)
            index = int(token)
            if index >= len(current):
                return SelectedResponseValue(found=False, value=None)
            current = current[index]
            continue
        return SelectedResponseValue(found=False, value=None)
    return SelectedResponseValue(found=True, value=current)


def _is_array_index(token: str) -> bool:
    return token == "0" or (token.isascii() and token.isdigit() and not token.startswith("0"))


def _decode_pointer_token(token: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        if token[index] != "~":
            decoded.append(token[index])
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise ResponseSelectionError("invalid JSON Pointer escape")
        decoded.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(decoded)
