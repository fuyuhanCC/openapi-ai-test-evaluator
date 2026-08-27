"""Pure conversion from captured Schemathesis requests to runner-ready cases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import JsonValue, TypeAdapter, ValidationError

from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec, OperationModel
from openapi_ai_test_evaluator.domain.test_case import (
    Assertion,
    AssertionOperator,
    RequestDefinition,
    RequestMode,
    RequestStep,
    TestCase,
)
from openapi_ai_test_evaluator.validation import detect_request_violations

_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)

# Preserve the intersection of Schemathesis' default status-oriented checks:
# status-code conformance, positive/negative data handling, and server-error rejection.
# A schema-valid request may still reference an unavailable or conflicting resource,
# while an intentionally invalid request must be rejected with a recognized 4xx status.
_POSITIVE_NON_SUCCESS_STATUSES = frozenset({401, 403, 404, 409})
_NEGATIVE_REJECTION_STATUSES = frozenset({400, 401, 403, 404, 405, 406, 409, 422, 428})


class CapturedGenerationMode(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class CapturedPhase(StrEnum):
    EXAMPLES = "examples"
    COVERAGE = "coverage"
    FUZZING = "fuzzing"


class AdaptationRejectionCode(StrEnum):
    CAPTURE_OPERATION_UNRESOLVED = "capture_operation_unresolved"
    CAPTURE_MODE_UNSUPPORTED = "capture_mode_unsupported"
    CAPTURE_PHASE_UNSUPPORTED = "capture_phase_unsupported"
    CAPTURE_COMPONENT_UNSUPPORTED = "capture_component_unsupported"
    UNKNOWN_OPERATION = "unknown_operation"
    METHOD_MISMATCH = "method_mismatch"
    PATH_MISMATCH = "path_mismatch"
    COOKIES_UNSUPPORTED = "cookies_unsupported"
    MEDIA_TYPE_UNSUPPORTED = "media_type_unsupported"
    VALUE_NOT_JSON = "value_not_json"
    RESERVED_RUNTIME_REFERENCE = "reserved_runtime_reference"
    REQUEST_SEMANTICS_UNSUPPORTED = "request_semantics_unsupported"
    POSITIVE_REQUEST_INVALID = "positive_request_invalid"
    NEGATIVE_REQUEST_VALID = "negative_request_valid"
    STATUS_ORACLE_UNAVAILABLE = "status_oracle_unavailable"
    CASE_CONTRACT_INVALID = "case_contract_invalid"


@dataclass(frozen=True, slots=True)
class CapturedSchemathesisCase:
    """Third-party-neutral snapshot taken from Schemathesis' public Case fields."""

    case_id: str
    operation_id: str
    method: str
    path: str
    mode: CapturedGenerationMode
    phase: CapturedPhase
    path_parameters: tuple[tuple[str, object], ...] = ()
    query: tuple[tuple[str, object], ...] = ()
    headers: tuple[tuple[str, object], ...] = ()
    cookies: tuple[tuple[str, object], ...] = ()
    body_present: bool = False
    body: object = None
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class AdaptationRejection:
    code: AdaptationRejectionCode
    message: str
    detail_code: str | None = None


@dataclass(frozen=True, slots=True)
class SchemathesisCaseAdaptation:
    case: TestCase | None
    rejections: tuple[AdaptationRejection, ...]

    @property
    def succeeded(self) -> bool:
        return self.case is not None


def adapt_schemathesis_case(
    captured: CapturedSchemathesisCase,
    spec: OpenAPISpec,
) -> SchemathesisCaseAdaptation:
    """Convert one captured stateless request without executing network I/O."""
    operation = spec.operations.get(captured.operation_id)
    if operation is None:
        return _rejected(
            AdaptationRejectionCode.UNKNOWN_OPERATION,
            f"operationId {captured.operation_id!r} is not present in the loaded OpenAPI",
        )

    preflight_rejections = _preflight_rejections(captured, operation)
    if preflight_rejections:
        return SchemathesisCaseAdaptation(case=None, rejections=preflight_rejections)

    try:
        request = _request_definition(captured)
    except _CapturedValueError as error:
        return _rejected(error.code, error.message)
    except ValidationError:
        return _rejected(
            AdaptationRejectionCode.CASE_CONTRACT_INVALID,
            "captured request could not form a valid runner request",
        )

    provisional_step = RequestStep(
        id="request",
        operation_id=captured.operation_id,
        request=request,
    )
    report = detect_request_violations(provisional_step, spec)
    if report.issues:
        return SchemathesisCaseAdaptation(
            case=None,
            rejections=tuple(
                AdaptationRejection(
                    code=AdaptationRejectionCode.REQUEST_SEMANTICS_UNSUPPORTED,
                    detail_code=issue.code,
                    message=issue.message,
                )
                for issue in report.issues
            ),
        )

    if captured.mode is CapturedGenerationMode.POSITIVE and report.violations:
        return _rejected(
            AdaptationRejectionCode.POSITIVE_REQUEST_INVALID,
            "Schemathesis marked the request positive, but it violates the loaded OpenAPI",
        )
    if captured.mode is CapturedGenerationMode.NEGATIVE and not report.violations:
        return _rejected(
            AdaptationRejectionCode.NEGATIVE_REQUEST_VALID,
            "Schemathesis marked the request negative, but no OpenAPI violation was detected",
        )

    if captured.mode is CapturedGenerationMode.NEGATIVE:
        request_data = request.model_dump(mode="json")
        request_data["mode"] = RequestMode.INTENTIONALLY_INVALID.value
        request_data["expected_violations"] = [
            violation.model_dump(mode="json") for violation in report.violations
        ]
        request = RequestDefinition.model_validate(request_data)

    expected_statuses = _expected_statuses(operation, captured.mode)
    if not expected_statuses:
        return _rejected(
            AdaptationRejectionCode.STATUS_ORACLE_UNAVAILABLE,
            f"{operation.operation_id} has no explicit response status for {captured.mode.value}",
        )

    assertions = _response_assertions(operation, expected_statuses)
    try:
        case = TestCase(
            id=captured.case_id,
            name=f"Schemathesis {captured.phase.value} {captured.mode.value} case",
            tags=[
                "source:schemathesis",
                f"phase:{captured.phase.value}",
                f"mode:{captured.mode.value}",
            ],
            steps=[
                RequestStep(
                    id="request",
                    operation_id=captured.operation_id,
                    request=request,
                    assertions=assertions,
                )
            ],
        )
    except ValidationError:
        return _rejected(
            AdaptationRejectionCode.CASE_CONTRACT_INVALID,
            "captured request could not form a valid runner TestCase",
        )
    return SchemathesisCaseAdaptation(case=case, rejections=())


def _preflight_rejections(
    captured: CapturedSchemathesisCase,
    operation: OperationModel,
) -> tuple[AdaptationRejection, ...]:
    rejections: list[AdaptationRejection] = []
    if captured.method.upper() != operation.method.upper():
        rejections.append(
            AdaptationRejection(
                code=AdaptationRejectionCode.METHOD_MISMATCH,
                message=(
                    f"captured method {captured.method!r} does not match "
                    f"{operation.operation_id} method {operation.method!r}"
                ),
            )
        )
    if captured.path != operation.path:
        rejections.append(
            AdaptationRejection(
                code=AdaptationRejectionCode.PATH_MISMATCH,
                message=(
                    f"captured path {captured.path!r} does not match "
                    f"{operation.operation_id} path {operation.path!r}"
                ),
            )
        )
    if captured.cookies:
        rejections.append(
            AdaptationRejection(
                code=AdaptationRejectionCode.COOKIES_UNSUPPORTED,
                message="cookie parameters are outside the V1 runner subset",
            )
        )
    if captured.body_present and _normalized_media_type(captured.media_type) != "application/json":
        rejections.append(
            AdaptationRejection(
                code=AdaptationRejectionCode.MEDIA_TYPE_UNSUPPORTED,
                message="captured request body is not application/json",
            )
        )
    return tuple(rejections)


def _request_definition(captured: CapturedSchemathesisCase) -> RequestDefinition:
    path = _validated_mapping(captured.path_parameters, "path")
    headers = _validated_mapping(captured.headers, "headers", case_insensitive=True)
    query = [
        {"name": name, "value": _validated_json_value(value, f"query.{name}")}
        for name, value in captured.query
    ]
    request_data: dict[str, Any] = {
        "path": path,
        "query": query,
        "headers": headers,
    }
    if captured.body_present:
        request_data["body"] = _validated_json_value(captured.body, "body")
    return RequestDefinition.model_validate(request_data)


def _validated_mapping(
    pairs: tuple[tuple[str, object], ...],
    location: str,
    *,
    case_insensitive: bool = False,
) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {}
    seen: set[str] = set()
    for name, value in pairs:
        key = name.casefold() if case_insensitive else name
        if key in seen:
            raise _CapturedValueError(
                AdaptationRejectionCode.CASE_CONTRACT_INVALID,
                f"captured {location} contains duplicate parameter {name!r}",
            )
        seen.add(key)
        values[name] = _validated_json_value(value, f"{location}.{name}")
    return values


def _validated_json_value(value: object, location: str) -> JsonValue:
    if _contains_runtime_reference(value):
        raise _CapturedValueError(
            AdaptationRejectionCode.RESERVED_RUNTIME_REFERENCE,
            f"captured {location} contains the reserved $var object shape",
        )
    try:
        return _JSON_VALUE_ADAPTER.validate_python(value, strict=True)
    except ValidationError as error:
        raise _CapturedValueError(
            AdaptationRejectionCode.VALUE_NOT_JSON,
            f"captured {location} is not a JSON value",
        ) from error


def _contains_runtime_reference(value: object) -> bool:
    if isinstance(value, dict):
        if "$var" in value:
            return True
        return any(_contains_runtime_reference(nested) for nested in value.values())
    if isinstance(value, list):
        return any(_contains_runtime_reference(nested) for nested in value)
    return False


def _expected_statuses(
    operation: OperationModel,
    mode: CapturedGenerationMode,
) -> tuple[int, ...]:
    def accepted(status: int) -> bool:
        if mode is CapturedGenerationMode.POSITIVE:
            return 200 <= status < 300 or status in _POSITIVE_NON_SUCCESS_STATUSES
        return status in _NEGATIVE_REJECTION_STATUSES

    statuses = sorted(
        int(status)
        for status in operation.responses
        if status.isdigit() and accepted(int(status))
    )
    return tuple(statuses)


def _response_assertions(
    operation: OperationModel,
    statuses: tuple[int, ...],
) -> list[Assertion]:
    if len(statuses) == 1:
        assertions = [Assertion(operator=AssertionOperator.STATUS_IS, expected=statuses[0])]
    else:
        assertions = [Assertion(operator=AssertionOperator.STATUS_IN, expected=list(statuses))]
    if any(operation.responses[str(status)].schema_definition is not None for status in statuses):
        assertions.append(Assertion(operator=AssertionOperator.SCHEMA_MATCHES))
    return assertions


def _normalized_media_type(media_type: str | None) -> str | None:
    if media_type is None:
        return None
    normalized = media_type.split(";", 1)[0].strip().casefold()
    return normalized or None


def _rejected(
    code: AdaptationRejectionCode,
    message: str,
) -> SchemathesisCaseAdaptation:
    return SchemathesisCaseAdaptation(
        case=None,
        rejections=(AdaptationRejection(code=code, message=message),),
    )


class _CapturedValueError(ValueError):
    def __init__(self, code: AdaptationRejectionCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)
