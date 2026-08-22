"""Coordinate one validated TestPlan request step through the HTTP pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from time import perf_counter_ns

from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.execution import (
    AssertionResult,
    ErrorCategory,
    ExecutionOutcome,
    ExtractionStatus,
    OutcomePolicy,
    StepPhase,
    StepResult,
    StructuredError,
)
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.domain.test_plan import (
    Assertion,
    AssertionOperator,
    CleanupStep,
    PlanDefaults,
    RequestMode,
    RequestStep,
)
from openapi_ai_test_evaluator.execution.assertions import execute_assertions
from openapi_ai_test_evaluator.execution.extractions import ExtractionBatch, execute_extractions
from openapi_ai_test_evaluator.execution.openapi_validation import OpenAPIContractValidator
from openapi_ai_test_evaluator.execution.request_builder import (
    PreparedRequest,
    RequestBuildError,
    build_request,
)
from openapi_ai_test_evaluator.execution.response_processor import (
    ProcessedResponse,
    process_response,
)
from openapi_ai_test_evaluator.execution.snapshots import (
    build_request_snapshot,
    build_response_snapshot,
)
from openapi_ai_test_evaluator.execution.transport import (
    HttpTransport,
    TransportFailure,
)


@dataclass(frozen=True, slots=True)
class StepExecution:
    """Stored step result plus raw in-memory values needed by scenario execution."""

    result: StepResult
    extracted_values: tuple[tuple[str, JsonValue], ...] = field(repr=False)
    prepared_request: PreparedRequest | None = field(repr=False)
    processed_response: ProcessedResponse | None = field(repr=False)


def execute_step(
    step: RequestStep,
    phase: StepPhase,
    spec: OpenAPISpec,
    defaults: PlanDefaults,
    variables: Mapping[str, JsonValue],
    validator: OpenAPIContractValidator,
    transport: HttpTransport,
) -> StepExecution:
    """Execute one step and convert expected failures into a stable StepResult."""
    started_at = perf_counter_ns()
    outcome_policy = _outcome_policy(step, phase)

    try:
        prepared_request = build_request(step, spec, variables, defaults)
    except RequestBuildError as error:
        return StepExecution(
            result=StepResult(
                phase=phase,
                step_id=step.id,
                operation_id=step.operation_id,
                outcome_policy=outcome_policy,
                outcome=ExecutionOutcome.ERROR,
                duration_ms=_elapsed_ms(started_at),
                retry_count=0,
                request=None,
                response=None,
                extractions=[],
                assertions=[],
                errors=[
                    _error(
                        1,
                        ErrorCategory.REQUEST_BUILD_FAILED,
                        error.location,
                        error.message,
                    )
                ],
            ),
            extracted_values=(),
            prepared_request=None,
            processed_response=None,
        )

    request_snapshot = build_request_snapshot(prepared_request)
    request_issues = validator.validate_request(prepared_request)
    if message := _request_contract_failure(step, len(request_issues)):
        return StepExecution(
            result=StepResult(
                phase=phase,
                step_id=step.id,
                operation_id=step.operation_id,
                outcome_policy=outcome_policy,
                outcome=ExecutionOutcome.ERROR,
                duration_ms=_elapsed_ms(started_at),
                retry_count=0,
                request=request_snapshot,
                response=None,
                extractions=[],
                assertions=[],
                errors=[
                    _error(
                        1,
                        ErrorCategory.REQUEST_BUILD_FAILED,
                        "request",
                        message,
                    )
                ],
            ),
            extracted_values=(),
            prepared_request=prepared_request,
            processed_response=None,
        )

    try:
        raw_response = transport.send(prepared_request)
    except TransportFailure as error:
        return StepExecution(
            result=StepResult(
                phase=phase,
                step_id=step.id,
                operation_id=step.operation_id,
                outcome_policy=outcome_policy,
                outcome=ExecutionOutcome.ERROR,
                duration_ms=_elapsed_ms(started_at),
                retry_count=0,
                request=request_snapshot,
                response=None,
                extractions=[],
                assertions=[],
                errors=[_error(1, error.category, error.location, error.message)],
            ),
            extracted_values=(),
            prepared_request=prepared_request,
            processed_response=None,
        )

    processed_response = process_response(prepared_request, raw_response, validator)
    assertion_results = execute_assertions(step.assertions, processed_response, variables)
    extraction_batch = execute_extractions(step.extract, processed_response)
    errors = _evaluation_errors(step.assertions, assertion_results, extraction_batch)

    return StepExecution(
        result=StepResult(
            phase=phase,
            step_id=step.id,
            operation_id=step.operation_id,
            outcome_policy=outcome_policy,
            outcome=_evaluation_outcome(assertion_results, extraction_batch),
            duration_ms=_elapsed_ms(started_at),
            retry_count=0,
            request=request_snapshot,
            response=build_response_snapshot(raw_response),
            extractions=list(extraction_batch.results),
            assertions=list(assertion_results),
            errors=errors,
        ),
        extracted_values=extraction_batch.values,
        prepared_request=prepared_request,
        processed_response=processed_response,
    )


def skip_step(step: RequestStep, phase: StepPhase) -> StepExecution:
    """Create an explicit skipped execution without building or sending a request."""
    return StepExecution(
        result=StepResult(
            phase=phase,
            step_id=step.id,
            operation_id=step.operation_id,
            outcome_policy=_outcome_policy(step, phase),
            outcome=ExecutionOutcome.SKIPPED,
            duration_ms=0,
            retry_count=0,
            request=None,
            response=None,
            extractions=[],
            assertions=[],
            errors=[],
        ),
        extracted_values=(),
        prepared_request=None,
        processed_response=None,
    )


def _outcome_policy(step: RequestStep, phase: StepPhase) -> OutcomePolicy:
    if isinstance(step, CleanupStep):
        if phase is not StepPhase.CLEANUP:
            raise ValueError("CleanupStep must execute in the cleanup phase")
        if step.ignore_errors:
            return OutcomePolicy.BEST_EFFORT
    elif phase is StepPhase.CLEANUP:
        raise ValueError("cleanup phase requires a CleanupStep")
    return OutcomePolicy.REQUIRED


def _request_contract_failure(step: RequestStep, issue_count: int) -> str | None:
    if step.request.mode is RequestMode.CONFORMANT and issue_count:
        return f"conformant request violates the OpenAPI contract ({issue_count} issue(s))"
    if step.request.mode is RequestMode.INTENTIONALLY_INVALID and not issue_count:
        return "intentionally_invalid request does not violate the OpenAPI contract"
    return None


def _evaluation_outcome(
    assertions: tuple[AssertionResult, ...],
    extraction_batch: ExtractionBatch,
) -> ExecutionOutcome:
    if any(result.outcome is ExecutionOutcome.ERROR for result in assertions) or any(
        result.status is ExtractionStatus.ERROR for result in extraction_batch.results
    ):
        return ExecutionOutcome.ERROR
    if any(result.outcome is ExecutionOutcome.FAILED for result in assertions) or any(
        result.required and result.status is ExtractionStatus.MISSING
        for result in extraction_batch.results
    ):
        return ExecutionOutcome.FAILED
    return ExecutionOutcome.PASSED


def _evaluation_errors(
    assertions: list[Assertion],
    assertion_results: tuple[AssertionResult, ...],
    extraction_batch: ExtractionBatch,
) -> list[StructuredError]:
    errors: list[StructuredError] = []
    for assertion, result in zip(assertions, assertion_results, strict=True):
        if result.outcome is ExecutionOutcome.PASSED:
            continue
        category, location, pointer = _assertion_error_location(assertion)
        errors.append(
            _error(
                len(errors) + 1,
                category,
                location,
                result.message or "assertion could not be satisfied",
                pointer=pointer,
                assertion_id=result.assertion_id,
            )
        )

    for issue in extraction_batch.issues:
        errors.append(
            _error(
                len(errors) + 1,
                ErrorCategory.EXTRACTION_FAILED,
                issue.source,
                issue.message,
                pointer=issue.pointer,
            )
        )
    return errors


def _assertion_error_location(
    assertion: Assertion,
) -> tuple[ErrorCategory, str, str | None]:
    if assertion.operator is AssertionOperator.STATUS_IS:
        return ErrorCategory.UNEXPECTED_STATUS, "response.status", None
    if assertion.operator is AssertionOperator.SCHEMA_MATCHES:
        return ErrorCategory.RESPONSE_SCHEMA_MISMATCH, "response.body", None
    assert assertion.actual is not None
    return ErrorCategory.ASSERTION_FAILED, assertion.actual.source, assertion.actual.pointer


def _error(
    index: int,
    category: ErrorCategory,
    location: str,
    message: str,
    *,
    pointer: str | None = None,
    assertion_id: str | None = None,
) -> StructuredError:
    return StructuredError(
        error_id=f"error-{index}",
        category=category,
        location=location,
        pointer=pointer,
        assertion_id=assertion_id,
        message=message,
        evidence=[],
    )


def _elapsed_ms(started_at: int) -> int:
    return max(0, (perf_counter_ns() - started_at) // 1_000_000)
