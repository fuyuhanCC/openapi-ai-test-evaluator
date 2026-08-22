"""Deterministic extraction of response values into runtime variables."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.execution import ExtractionResult, ExtractionStatus
from openapi_ai_test_evaluator.domain.test_plan import Extraction
from openapi_ai_test_evaluator.execution.response_processor import ProcessedResponse
from openapi_ai_test_evaluator.execution.response_selection import (
    ResponseSelectionError,
    response_pointer_is_sensitive,
    select_response_value,
)
from openapi_ai_test_evaluator.execution.snapshots import (
    REDACTED_VALUE,
    sanitize_json_value,
)

ExtractionSource = Literal["response.body", "response.headers"]


@dataclass(frozen=True, slots=True)
class ExtractionIssue:
    """A required missing value or an extraction evaluation error."""

    variable: str
    source: ExtractionSource
    pointer: str
    message: str


@dataclass(frozen=True, slots=True)
class ExtractionBatch:
    """Stored results plus raw values that may enter the runtime variable scope."""

    results: tuple[ExtractionResult, ...]
    values: tuple[tuple[str, JsonValue], ...]
    issues: tuple[ExtractionIssue, ...]


def execute_extractions(
    extractions: Sequence[Extraction],
    response: ProcessedResponse,
) -> ExtractionBatch:
    """Execute response extractions in plan order without mutating variable scope."""
    results: list[ExtractionResult] = []
    values: list[tuple[str, JsonValue]] = []
    issues: list[ExtractionIssue] = []

    for extraction in extractions:
        try:
            selection = select_response_value(
                response,
                extraction.source,
                extraction.pointer,
            )
        except ResponseSelectionError as error:
            message = str(error)
            results.append(_unsuccessful_result(extraction, ExtractionStatus.ERROR))
            issues.append(_issue(extraction, message))
            continue

        if not selection.found:
            results.append(_unsuccessful_result(extraction, ExtractionStatus.MISSING))
            if extraction.required:
                issues.append(_issue(extraction, "required response value is missing"))
            continue

        raw_value = cast(JsonValue, selection.value)
        stored_value, redacted = _stored_extraction_value(extraction, raw_value)
        results.append(
            ExtractionResult(
                variable=extraction.variable,
                source=extraction.source,
                pointer=extraction.pointer,
                required=extraction.required,
                status=ExtractionStatus.EXTRACTED,
                value=stored_value,
                redacted=redacted,
            )
        )
        values.append((extraction.variable, raw_value))

    return ExtractionBatch(
        results=tuple(results),
        values=tuple(values),
        issues=tuple(issues),
    )


def _unsuccessful_result(
    extraction: Extraction,
    status: Literal[ExtractionStatus.MISSING, ExtractionStatus.ERROR],
) -> ExtractionResult:
    return ExtractionResult(
        variable=extraction.variable,
        source=extraction.source,
        pointer=extraction.pointer,
        required=extraction.required,
        status=status,
        value=None,
        redacted=False,
    )


def _issue(extraction: Extraction, message: str) -> ExtractionIssue:
    return ExtractionIssue(
        variable=extraction.variable,
        source=extraction.source,
        pointer=extraction.pointer,
        message=message,
    )


def _stored_extraction_value(
    extraction: Extraction,
    value: JsonValue,
) -> tuple[JsonValue, bool]:
    if response_pointer_is_sensitive(extraction.source, extraction.pointer):
        return REDACTED_VALUE, True
    sanitized = sanitize_json_value(value)
    return sanitized, sanitized != value
