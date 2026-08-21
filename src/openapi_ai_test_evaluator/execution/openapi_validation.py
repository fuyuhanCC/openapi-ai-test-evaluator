"""Runtime OpenAPI request and response contract validation."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from openapi_core import OpenAPI
from openapi_core.configurations import Config
from openapi_core.templating.paths.finders import APICallPathFinder
from openapi_core.templating.paths.iterators import SimpleServersIterator

from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec, OperationModel
from openapi_ai_test_evaluator.execution.openapi_adapters import (
    adapt_openapi_request,
    adapt_openapi_response,
)
from openapi_ai_test_evaluator.execution.request_builder import PreparedRequest
from openapi_ai_test_evaluator.execution.transport import TransportResponse


class OpenAPIValidationSubject(StrEnum):
    REQUEST = "request"
    RESPONSE = "response"


@dataclass(frozen=True, slots=True)
class OpenAPIValidationIssue:
    """Stable internal representation of one openapi-core validation error."""

    subject: OpenAPIValidationSubject
    error_type: str
    message: str
    details: dict[str, Any] | None


class _RuntimeTargetPathFinder(APICallPathFinder):
    """Resolve operations by method/path while allowing an explicit runtime target."""

    servers_iterator = SimpleServersIterator()


class OpenAPIContractValidator:
    """Validate prepared exchanges against one loaded OpenAPI document."""

    def __init__(self, spec: OpenAPISpec, base_url: str) -> None:
        self._spec = spec
        self._base_url = base_url
        self._openapi = OpenAPI.from_dict(
            spec.document,
            config=Config(path_finder_cls=_RuntimeTargetPathFinder),
        )

    def validate_request(
        self,
        request: PreparedRequest,
    ) -> tuple[OpenAPIValidationIssue, ...]:
        """Return every request-contract issue without raising the first one."""
        adapter = adapt_openapi_request(request, self._operation(request), self._base_url)
        return _collect_issues(
            OpenAPIValidationSubject.REQUEST,
            self._openapi.iter_request_errors(adapter),
        )

    def validate_response(
        self,
        request: PreparedRequest,
        response: TransportResponse,
    ) -> tuple[OpenAPIValidationIssue, ...]:
        """Return every response-contract issue for a completed HTTP exchange."""
        request_adapter = adapt_openapi_request(
            request,
            self._operation(request),
            self._base_url,
        )
        response_adapter = adapt_openapi_response(response)
        return _collect_issues(
            OpenAPIValidationSubject.RESPONSE,
            self._openapi.iter_response_errors(request_adapter, response_adapter),
        )

    def _operation(self, request: PreparedRequest) -> OperationModel:
        try:
            return self._spec.operations[request.operation_id]
        except KeyError as error:
            raise ValueError(
                f"prepared request references unknown operation {request.operation_id!r}"
            ) from error


def _collect_issues(
    subject: OpenAPIValidationSubject,
    errors: Iterable[Exception],
) -> tuple[OpenAPIValidationIssue, ...]:
    return tuple(_to_issue(subject, error) for error in errors)


def _to_issue(
    subject: OpenAPIValidationSubject,
    error: Exception,
) -> OpenAPIValidationIssue:
    raw_details = getattr(error, "details", None)
    details = deepcopy(raw_details) if isinstance(raw_details, dict) else None
    return OpenAPIValidationIssue(
        subject=subject,
        error_type=type(error).__name__,
        message=str(error),
        details=details,
    )
