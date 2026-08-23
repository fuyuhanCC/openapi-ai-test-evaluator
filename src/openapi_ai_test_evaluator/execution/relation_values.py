"""Select raw and sanitized values from executed scenario steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.execution import RelationValueSnapshot
from openapi_ai_test_evaluator.domain.test_case import RelationFieldReference
from openapi_ai_test_evaluator.execution.response_selection import (
    ResponseSelectionError,
    SelectedResponseValue,
    pointer_tokens,
    response_pointer_is_sensitive,
    select_json_pointer_value,
    select_response_value,
)
from openapi_ai_test_evaluator.execution.snapshots import (
    REDACTED_VALUE,
    is_sensitive_name,
    sanitize_json_value,
)
from openapi_ai_test_evaluator.execution.step_executor import StepExecution


class RelationValueSelectionError(ValueError):
    """A declared relation value is unavailable or cannot be selected."""


@dataclass(frozen=True, slots=True)
class SelectedRelationValue:
    """Raw comparison value paired with its safe artifact representation."""

    snapshot: RelationValueSnapshot
    raw_value: JsonValue = field(repr=False)


def select_relation_value(
    execution: StepExecution,
    reference: RelationFieldReference,
) -> SelectedRelationValue:
    """Select one relation value from an executed request or response."""
    try:
        selection = _select(execution, reference)
    except ResponseSelectionError as error:
        raise RelationValueSelectionError(str(error)) from error

    if not selection.found:
        raise RelationValueSelectionError(
            f"{reference.location} value at {reference.pointer!r} is missing"
        )

    raw_value = cast(JsonValue, selection.value)
    return SelectedRelationValue(
        snapshot=RelationValueSnapshot(
            step_id=execution.result.step_id,
            location=reference.location,
            pointer=reference.pointer,
            value=_stored_value(reference, raw_value),
        ),
        raw_value=raw_value,
    )


def _select(
    execution: StepExecution,
    reference: RelationFieldReference,
) -> SelectedResponseValue:
    if reference.location == "request.body":
        if execution.prepared_request is None:
            raise RelationValueSelectionError("request was not prepared")
        if execution.prepared_request.json_body is None:
            return SelectedResponseValue(found=False, value=None)
        assert reference.pointer is not None
        return select_json_pointer_value(
            execution.prepared_request.json_body,
            reference.pointer,
        )

    if execution.processed_response is None:
        raise RelationValueSelectionError("response is unavailable")
    if reference.location == "response.body":
        return select_response_value(
            execution.processed_response,
            "response.body",
            reference.pointer,
        )
    return select_response_value(
        execution.processed_response,
        "response.status",
        reference.pointer,
    )


def _stored_value(reference: RelationFieldReference, value: JsonValue) -> JsonValue:
    if _reference_is_sensitive(reference):
        return REDACTED_VALUE
    return sanitize_json_value(value)


def _reference_is_sensitive(reference: RelationFieldReference) -> bool:
    if reference.location.startswith("response."):
        source = "response.body" if reference.location == "response.body" else "response.status"
        return response_pointer_is_sensitive(source, reference.pointer)
    if not reference.pointer:
        return False
    try:
        tokens = pointer_tokens(reference.pointer)
    except ResponseSelectionError:
        return False
    return bool(tokens) and is_sensitive_name(tokens[-1])
