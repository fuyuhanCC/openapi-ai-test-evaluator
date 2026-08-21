"""Coordinate response contract validation and body parsing."""

from __future__ import annotations

from dataclasses import dataclass

from openapi_ai_test_evaluator.execution.openapi_validation import (
    OpenAPIContractValidator,
    OpenAPIValidationIssue,
)
from openapi_ai_test_evaluator.execution.request_builder import PreparedRequest
from openapi_ai_test_evaluator.execution.response_parser import (
    ResponseData,
    ResponseParseError,
    parse_response,
)
from openapi_ai_test_evaluator.execution.transport import TransportResponse


@dataclass(frozen=True, slots=True)
class ResponseParseIssue:
    """Stable, non-exception representation of a response parsing failure."""

    location: str
    message: str


@dataclass(frozen=True, slots=True)
class ProcessedResponse:
    """All deterministic response-processing outputs for one HTTP exchange."""

    raw: TransportResponse
    data: ResponseData | None
    contract_issues: tuple[OpenAPIValidationIssue, ...]
    parse_issue: ResponseParseIssue | None

    def __post_init__(self) -> None:
        if (self.data is None) == (self.parse_issue is None):
            raise ValueError("exactly one of data or parse_issue must be present")


def process_response(
    request: PreparedRequest,
    response: TransportResponse,
    validator: OpenAPIContractValidator,
) -> ProcessedResponse:
    """Validate and parse one raw response without losing partial evidence."""
    contract_issues = validator.validate_response(request, response)
    try:
        data = parse_response(response)
    except ResponseParseError as error:
        return ProcessedResponse(
            raw=response,
            data=None,
            contract_issues=contract_issues,
            parse_issue=ResponseParseIssue(
                location=error.location,
                message=error.message,
            ),
        )

    return ProcessedResponse(
        raw=response,
        data=data,
        contract_issues=contract_issues,
        parse_issue=None,
    )
