"""Strict contracts for deterministic API test-case execution results."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from openapi_ai_test_evaluator.domain.contracts import ContractModel, Identifier, JsonPointer
from openapi_ai_test_evaluator.domain.test_case import (
    AssertionOperator,
    RelationKind,
    RelationType,
)

NonNegativeInt = Annotated[int, Field(ge=0)]


class ExecutionOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class RelationOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    NOT_APPLICABLE = "not_applicable"
    SKIPPED = "skipped"


class ExtractionStatus(StrEnum):
    EXTRACTED = "extracted"
    MISSING = "missing"
    ERROR = "error"
    SKIPPED = "skipped"


class StepPhase(StrEnum):
    SETUP = "setup"
    MAIN = "main"
    CLEANUP = "cleanup"


class OutcomePolicy(StrEnum):
    REQUIRED = "required"
    BEST_EFFORT = "best_effort"


class FaultTriggerStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    TRIGGERED = "triggered"
    NOT_TRIGGERED = "not_triggered"
    UNKNOWN = "unknown"


class ComparisonOperator(StrEnum):
    EQUALS = "equals"
    SET_EQUALS = "set_equals"
    SUBSET = "subset"
    PREFIX = "prefix"
    ONE_OF = "one_of"
    UNCHANGED = "unchanged"


class ErrorCategory(StrEnum):
    CASE_INVALID = "case_invalid"
    REQUEST_BUILD_FAILED = "request_build_failed"
    TRANSPORT_ERROR = "transport_error"
    TIMEOUT = "timeout"
    UNEXPECTED_STATUS = "unexpected_status"
    RESPONSE_SCHEMA_MISMATCH = "response_schema_mismatch"
    ASSERTION_FAILED = "assertion_failed"
    EXTRACTION_FAILED = "extraction_failed"
    METAMORPHIC_RELATION_VIOLATED = "metamorphic_relation_violated"
    LIFECYCLE_CONSISTENCY_VIOLATED = "lifecycle_consistency_violated"
    SUT_UNAVAILABLE = "sut_unavailable"
    RESPONSE_TOO_LARGE = "response_too_large"
    RUNNER_INTERNAL_ERROR = "runner_internal_error"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class BodySnapshot(ContractModel):
    """A sanitized, size-bounded request or response body."""

    media_type: str | None
    value: JsonValue | None
    size_bytes: NonNegativeInt
    truncated: bool


class QueryParameterSnapshot(ContractModel):
    name: str = Field(min_length=1)
    value: JsonValue


class RequestSnapshot(ContractModel):
    method: HttpMethod
    path: str = Field(pattern=r"^/")
    query: list[QueryParameterSnapshot] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    body: BodySnapshot


class ResponseSnapshot(ContractModel):
    status_code: int = Field(ge=100, le=599)
    headers: dict[str, str] = Field(default_factory=dict)
    body: BodySnapshot


class Evidence(ContractModel):
    name: str = Field(min_length=1)
    value: JsonValue


class StructuredError(ContractModel):
    error_id: Identifier
    category: ErrorCategory
    location: str = Field(min_length=1)
    pointer: JsonPointer | None = None
    assertion_id: Identifier | None = None
    message: str = Field(min_length=1)
    evidence: list[Evidence] = Field(default_factory=list)


class ExtractionResult(ContractModel):
    variable: str = Field(min_length=1)
    source: Literal["response.body", "response.headers"]
    pointer: JsonPointer
    required: bool
    status: ExtractionStatus
    value: JsonValue | None
    redacted: bool

    @model_validator(mode="after")
    def validate_stored_value(self) -> Self:
        if self.status is not ExtractionStatus.EXTRACTED and self.value is not None:
            raise ValueError("only extracted values may store a value")
        if self.status is not ExtractionStatus.EXTRACTED and self.redacted:
            raise ValueError("only extracted values may be marked as redacted")
        if self.redacted and not _contains_redaction_marker(self.value):
            raise ValueError("redacted extraction values must contain '[REDACTED]'")
        return self


def _contains_redaction_marker(value: JsonValue | None) -> bool:
    if value == "[REDACTED]":
        return True
    if isinstance(value, list):
        return any(_contains_redaction_marker(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_redaction_marker(item) for item in value.values())
    return False


class AssertionIssue(ContractModel):
    location: JsonPointer
    keyword: str = Field(min_length=1)
    message: str = Field(min_length=1)


class AssertionResult(ContractModel):
    assertion_id: Identifier
    operator: AssertionOperator
    outcome: ExecutionOutcome
    actual: JsonValue | None
    expected: JsonValue | None
    message: str | None
    issues: list[AssertionIssue] = Field(default_factory=list)


class RelationValueSnapshot(ContractModel):
    step_id: Identifier
    location: Literal["request.body", "response.body", "response.status"]
    pointer: JsonPointer | None = None
    value: JsonValue

    @model_validator(mode="after")
    def require_pointer_for_body(self) -> Self:
        if self.location.endswith(".body") and self.pointer is None:
            raise ValueError("body relation values require a JSON pointer")
        if self.location == "response.status" and self.pointer is not None:
            raise ValueError("response.status relation values cannot have a JSON pointer")
        return self


class RelationComparisonResult(ContractModel):
    comparison_id: Identifier
    operator: ComparisonOperator
    outcome: ExecutionOutcome
    source: RelationValueSnapshot
    follow_up: RelationValueSnapshot
    expected: JsonValue | None = None
    message: str | None

    @model_validator(mode="after")
    def validate_expected_value(self) -> Self:
        if self.operator is ComparisonOperator.ONE_OF:
            if not isinstance(self.expected, list) or not self.expected:
                raise ValueError("one_of comparisons require a non-empty expected list")
        elif self.expected is not None:
            raise ValueError("only one_of comparisons accept an expected value")
        return self


class RelationResult(ContractModel):
    relation_id: Identifier
    kind: RelationKind
    type: RelationType
    source_step: Identifier
    follow_up_step: Identifier
    baseline_step: Identifier | None
    outcome: RelationOutcome
    message: str | None = None
    comparisons: list[RelationComparisonResult] = Field(default_factory=list)
    errors: list[StructuredError] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_relation(self) -> Self:
        if self.kind is not self.type.kind:
            raise ValueError(
                f"relation kind {self.kind.value!r} does not match type {self.type.value!r}"
            )
        comparison_ids = [comparison.comparison_id for comparison in self.comparisons]
        if len(comparison_ids) != len(set(comparison_ids)):
            raise ValueError("comparison IDs must be unique within a relation result")
        if self.outcome in {RelationOutcome.PASSED, RelationOutcome.FAILED}:
            if not self.comparisons:
                raise ValueError("evaluated relation results require at least one comparison")
        if self.outcome is RelationOutcome.NOT_APPLICABLE and not self.message:
            raise ValueError("not_applicable relation results require a message")
        return self


class FaultObservation(ContractModel):
    configured_fault_id: Identifier | None
    trigger_status: FaultTriggerStatus
    trigger_count: NonNegativeInt

    @model_validator(mode="after")
    def validate_fault_state(self) -> Self:
        if self.trigger_status is FaultTriggerStatus.NOT_CONFIGURED:
            if self.configured_fault_id is not None or self.trigger_count != 0:
                raise ValueError(
                    "not_configured requires no configured fault and a zero trigger count"
                )
            return self

        if self.configured_fault_id is None:
            raise ValueError("configured fault status requires configured_fault_id")
        if self.trigger_status is FaultTriggerStatus.TRIGGERED and self.trigger_count == 0:
            raise ValueError("triggered faults require a positive trigger_count")
        if self.trigger_status is FaultTriggerStatus.NOT_TRIGGERED and self.trigger_count != 0:
            raise ValueError("not_triggered faults require a zero trigger_count")
        return self


class StepResult(ContractModel):
    phase: StepPhase
    step_id: Identifier
    operation_id: str = Field(min_length=1)
    outcome_policy: OutcomePolicy
    outcome: ExecutionOutcome
    duration_ms: NonNegativeInt
    retry_count: Literal[0] = 0
    request: RequestSnapshot | None
    response: ResponseSnapshot | None
    extractions: list[ExtractionResult] = Field(default_factory=list)
    assertions: list[AssertionResult] = Field(default_factory=list)
    errors: list[StructuredError] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_step_result(self) -> Self:
        if self.outcome_policy is OutcomePolicy.BEST_EFFORT and self.phase is not StepPhase.CLEANUP:
            raise ValueError("best_effort is valid only for cleanup step results")
        if self.response is not None and self.request is None:
            raise ValueError("a response snapshot requires a request snapshot")
        if self.outcome is ExecutionOutcome.SKIPPED and self.response is not None:
            raise ValueError("skipped steps cannot contain a response snapshot")

        assertion_ids = [assertion.assertion_id for assertion in self.assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("assertion IDs must be unique within a step result")
        extraction_names = [extraction.variable for extraction in self.extractions]
        if len(extraction_names) != len(set(extraction_names)):
            raise ValueError("extraction variables must be unique within a step result")
        _validate_unique_error_ids(self.errors)
        return self


class TestCaseResult(ContractModel):
    case_id: Identifier
    outcome: ExecutionOutcome
    steps: list[StepResult] = Field(min_length=1)
    relations: list[RelationResult] = Field(default_factory=list)
    errors: list[StructuredError] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_test_case_result(self) -> Self:
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step IDs must be unique within a test case result")

        relation_ids = [relation.relation_id for relation in self.relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("relation IDs must be unique within a test case result")

        known_step_ids = set(step_ids)
        for relation in self.relations:
            referenced_ids = {relation.source_step, relation.follow_up_step}
            if relation.baseline_step is not None:
                referenced_ids.add(relation.baseline_step)
            if missing := referenced_ids - known_step_ids:
                missing_list = ", ".join(sorted(missing))
                raise ValueError(
                    f"relation result {relation.relation_id} references unknown steps: "
                    f"{missing_list}"
                )

        _validate_unique_error_ids(self.errors)
        return self


class RunResult(ContractModel):
    schema_version: Literal["2.0"]
    kind: Literal["RunResult"]
    run_id: Identifier
    batch_name: Identifier
    spec_id: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime
    duration_ms: NonNegativeInt
    outcome: ExecutionOutcome
    fault: FaultObservation
    cases: list[TestCaseResult] = Field(min_length=1)
    errors: list[StructuredError] = Field(default_factory=list)

    @field_validator("started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("execution timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_run_result(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case IDs must be unique within a run result")
        _validate_unique_error_ids(self.errors)
        return self


def _validate_unique_error_ids(errors: list[StructuredError]) -> None:
    error_ids = [error.error_id for error in errors]
    if len(error_ids) != len(set(error_ids)):
        raise ValueError("error IDs must be unique within their owning result")
