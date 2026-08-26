"""Pure response mutation operations used by the fault proxy."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from email.message import Message
from enum import StrEnum

from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.execution import HttpMethod
from openapi_ai_test_evaluator.domain.fault import (
    DuplicateJsonArrayItemMutation,
    FaultDefinition,
    RemoveJsonValueMutation,
    ReplaceJsonValueMutation,
    ReplaceStatusMutation,
)
from openapi_ai_test_evaluator.execution.response_selection import (
    ResponseSelectionError,
    pointer_tokens,
)


@dataclass(frozen=True, slots=True)
class FaultRequestContext:
    method: HttpMethod
    path: str


@dataclass(frozen=True, slots=True)
class FaultResponse:
    status_code: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


class FaultApplicationReason(StrEnum):
    APPLIED = "applied"
    NO_ACTIVE_FAULT = "no_active_fault"
    METHOD_MISMATCH = "method_mismatch"
    PATH_MISMATCH = "path_mismatch"
    STATUS_MISMATCH = "status_mismatch"
    MEDIA_TYPE_MISMATCH = "media_type_mismatch"
    RESPONSE_NOT_JSON = "response_not_json"
    INVALID_JSON = "invalid_json"
    POINTER_NOT_FOUND = "pointer_not_found"
    TARGET_NOT_ARRAY = "target_not_array"
    INDEX_OUT_OF_RANGE = "index_out_of_range"
    NO_CHANGE = "no_change"


@dataclass(frozen=True, slots=True)
class FaultApplication:
    response: FaultResponse
    triggered: bool
    reason: FaultApplicationReason


def apply_response_fault(
    definition: FaultDefinition,
    request: FaultRequestContext,
    response: FaultResponse,
) -> FaultApplication:
    """Apply one fault without mutating the supplied response object."""
    mismatch = _match_failure(definition, request, response)
    if mismatch is not None:
        return _not_triggered(response, mismatch)

    mutation = definition.mutation
    if isinstance(mutation, ReplaceStatusMutation):
        if response.status_code == mutation.status_code:
            return _not_triggered(response, FaultApplicationReason.NO_CHANGE)
        return FaultApplication(
            response=FaultResponse(
                status_code=mutation.status_code,
                headers=response.headers,
                body=response.body,
            ),
            triggered=True,
            reason=FaultApplicationReason.APPLIED,
        )

    media_type = _response_media_type(response.headers)
    if not _is_json_media_type(media_type):
        return _not_triggered(response, FaultApplicationReason.RESPONSE_NOT_JSON)
    try:
        body = json.loads(response.body, parse_constant=_reject_nonstandard_number)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _not_triggered(response, FaultApplicationReason.INVALID_JSON)

    mutated_body, failure = _mutate_json_body(body, mutation)
    if failure is not None:
        return _not_triggered(response, failure)
    if _json_values_equal(mutated_body, body):
        return _not_triggered(response, FaultApplicationReason.NO_CHANGE)

    encoded_body = json.dumps(
        mutated_body,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return FaultApplication(
        response=FaultResponse(
            status_code=response.status_code,
            headers=_replace_content_length(response.headers, len(encoded_body)),
            body=encoded_body,
        ),
        triggered=True,
        reason=FaultApplicationReason.APPLIED,
    )


def _match_failure(
    definition: FaultDefinition,
    request: FaultRequestContext,
    response: FaultResponse,
) -> FaultApplicationReason | None:
    matcher = definition.matcher
    if request.method != matcher.method:
        return FaultApplicationReason.METHOD_MISMATCH
    if re.fullmatch(matcher.path_regex, request.path) is None:
        return FaultApplicationReason.PATH_MISMATCH
    if matcher.response_statuses and response.status_code not in matcher.response_statuses:
        return FaultApplicationReason.STATUS_MISMATCH
    if (
        matcher.response_media_type is not None
        and _response_media_type(response.headers) != matcher.response_media_type.casefold()
    ):
        return FaultApplicationReason.MEDIA_TYPE_MISMATCH
    return None


def _mutate_json_body(
    body: JsonValue,
    mutation: RemoveJsonValueMutation | ReplaceJsonValueMutation | DuplicateJsonArrayItemMutation,
) -> tuple[JsonValue, FaultApplicationReason | None]:
    mutated = deepcopy(body)
    if isinstance(mutation, ReplaceJsonValueMutation) and mutation.pointer == "":
        return deepcopy(mutation.value), None

    try:
        tokens = pointer_tokens(mutation.pointer)
    except ResponseSelectionError:
        return body, FaultApplicationReason.POINTER_NOT_FOUND

    if isinstance(mutation, DuplicateJsonArrayItemMutation):
        target, failure = _resolve_pointer(mutated, tokens)
        if failure is not None:
            return body, failure
        if not isinstance(target, list):
            return body, FaultApplicationReason.TARGET_NOT_ARRAY
        if mutation.index >= len(target):
            return body, FaultApplicationReason.INDEX_OUT_OF_RANGE
        target.insert(mutation.index + 1, deepcopy(target[mutation.index]))
        return mutated, None

    parent, token, failure = _resolve_parent(mutated, tokens)
    if failure is not None:
        return body, failure
    assert token is not None

    if isinstance(parent, dict):
        if token not in parent:
            return body, FaultApplicationReason.POINTER_NOT_FOUND
        if isinstance(mutation, RemoveJsonValueMutation):
            del parent[token]
        else:
            parent[token] = deepcopy(mutation.value)
        return mutated, None

    if isinstance(parent, list):
        index = _array_index(token, len(parent))
        if index is None:
            return body, FaultApplicationReason.POINTER_NOT_FOUND
        if isinstance(mutation, RemoveJsonValueMutation):
            del parent[index]
        else:
            parent[index] = deepcopy(mutation.value)
        return mutated, None

    return body, FaultApplicationReason.POINTER_NOT_FOUND


def _resolve_parent(
    value: JsonValue, tokens: list[str]
) -> tuple[JsonValue, str | None, FaultApplicationReason | None]:
    if not tokens:
        return value, None, FaultApplicationReason.POINTER_NOT_FOUND
    parent, failure = _resolve_pointer(value, tokens[:-1])
    return parent, tokens[-1], failure


def _resolve_pointer(
    value: JsonValue, tokens: list[str]
) -> tuple[JsonValue, FaultApplicationReason | None]:
    current = value
    for token in tokens:
        if isinstance(current, dict):
            if token not in current:
                return value, FaultApplicationReason.POINTER_NOT_FOUND
            current = current[token]
            continue
        if isinstance(current, list):
            index = _array_index(token, len(current))
            if index is None:
                return value, FaultApplicationReason.POINTER_NOT_FOUND
            current = current[index]
            continue
        return value, FaultApplicationReason.POINTER_NOT_FOUND
    return current, None


def _array_index(token: str, length: int) -> int | None:
    if token == "0":
        return 0 if length > 0 else None
    if not token.isascii() or not token.isdigit() or token.startswith("0"):
        return None
    index = int(token)
    return index if index < length else None


def _response_media_type(headers: tuple[tuple[str, str], ...]) -> str | None:
    for name, value in headers:
        if name.casefold() != "content-type":
            continue
        message = Message()
        message["content-type"] = value
        return message.get_content_type().casefold()
    return None


def _is_json_media_type(media_type: str | None) -> bool:
    return media_type == "application/json" or (
        media_type is not None and media_type.endswith("+json")
    )


def _replace_content_length(
    headers: tuple[tuple[str, str], ...], body_length: int
) -> tuple[tuple[str, str], ...]:
    retained = tuple(
        (name, value) for name, value in headers if name.casefold() != "content-length"
    )
    return (*retained, ("content-length", str(body_length)))


def _reject_nonstandard_number(value: str) -> None:
    raise json.JSONDecodeError(f"non-standard JSON number {value}", value, 0)


def _json_values_equal(left: JsonValue, right: JsonValue) -> bool:
    """Compare JSON values without treating booleans and numbers as equal."""
    options = {
        "allow_nan": False,
        "ensure_ascii": False,
        "separators": (",", ":"),
        "sort_keys": True,
    }
    return json.dumps(left, **options) == json.dumps(right, **options)


def _not_triggered(response: FaultResponse, reason: FaultApplicationReason) -> FaultApplication:
    return FaultApplication(response=response, triggered=False, reason=reason)


__all__ = [
    "FaultApplication",
    "FaultApplicationReason",
    "FaultRequestContext",
    "FaultResponse",
    "apply_response_fault",
]
