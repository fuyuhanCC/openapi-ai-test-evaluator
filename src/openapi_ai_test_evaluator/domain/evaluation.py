"""Strict contracts for one generator suite evaluation."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from openapi_ai_test_evaluator.domain.contracts import ContractModel, Identifier
from openapi_ai_test_evaluator.domain.generation import GenerationTokenUsage

NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]
Rate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
NonNegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class GeneratorKind(StrEnum):
    LLM = "llm"
    SCHEMA_TOOL = "schema_tool"


class FaultEvaluationOutcome(StrEnum):
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    NOT_TRIGGERED = "not_triggered"
    NO_ELIGIBLE_CASE = "no_eligible_case"
    INCONCLUSIVE = "inconclusive"


class GeneratorMetadata(ContractModel):
    kind: GeneratorKind
    name: str = Field(min_length=1)
    version: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    source_record_id: str | None = Field(default=None, min_length=1)
    generation_request_count: NonNegativeInt
    generation_duration_ms: NonNegativeInt | None = None
    token_usage: GenerationTokenUsage = Field(default_factory=GenerationTokenUsage)
    estimated_cost_usd: NonNegativeFloat | None = None


class CaseAdmissionMetrics(ContractModel):
    received_case_count: NonNegativeInt
    admitted_case_count: PositiveInt
    rejected_case_count: NonNegativeInt
    admission_rate: Rate

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.received_case_count != self.admitted_case_count + self.rejected_case_count:
            raise ValueError("received cases must equal admitted plus rejected cases")
        expected_rate = self.admitted_case_count / self.received_case_count
        if abs(self.admission_rate - expected_rate) > 1e-12:
            raise ValueError("admission_rate does not match the case counts")
        return self


class SuiteExecutionMetrics(ContractModel):
    admitted_case_count: PositiveInt
    clean_passed_case_count: NonNegativeInt
    clean_failed_case_count: NonNegativeInt
    clean_error_case_count: NonNegativeInt
    clean_skipped_case_count: NonNegativeInt
    executable_case_rate: Rate
    clean_false_positive_rate: Rate | None
    eligible_operation_count: NonNegativeInt
    covered_operation_count: NonNegativeInt
    operation_coverage_rate: Rate | None
    clean_request_count: NonNegativeInt
    fault_request_count: NonNegativeInt
    total_request_count: NonNegativeInt
    execution_duration_ms: NonNegativeInt

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        clean_total = (
            self.clean_passed_case_count
            + self.clean_failed_case_count
            + self.clean_error_case_count
            + self.clean_skipped_case_count
        )
        if clean_total != self.admitted_case_count:
            raise ValueError("clean outcome counts must equal admitted_case_count")
        deterministic_count = self.clean_passed_case_count + self.clean_failed_case_count
        if abs(self.executable_case_rate - deterministic_count / clean_total) > 1e-12:
            raise ValueError("executable_case_rate does not match clean outcome counts")
        expected_false_positive = (
            self.clean_failed_case_count / deterministic_count if deterministic_count else None
        )
        if not _optional_rate_matches(self.clean_false_positive_rate, expected_false_positive):
            raise ValueError("clean_false_positive_rate does not match clean outcome counts")
        if self.covered_operation_count > self.eligible_operation_count:
            raise ValueError("covered operations cannot exceed eligible operations")
        expected_coverage = (
            self.covered_operation_count / self.eligible_operation_count
            if self.eligible_operation_count
            else None
        )
        if not _optional_rate_matches(self.operation_coverage_rate, expected_coverage):
            raise ValueError("operation_coverage_rate does not match operation counts")
        if self.total_request_count != self.clean_request_count + self.fault_request_count:
            raise ValueError("total requests must equal clean plus fault requests")
        return self


class FaultEvaluation(ContractModel):
    fault_id: Identifier
    run_id: Identifier
    outcome: FaultEvaluationOutcome
    trigger_count: NonNegativeInt
    request_count: NonNegativeInt
    triggered_case_ids: list[Identifier] = Field(default_factory=list)
    eligible_triggered_case_ids: list[Identifier] = Field(default_factory=list)
    detected_case_ids: list[Identifier] = Field(default_factory=list)
    errored_case_ids: list[Identifier] = Field(default_factory=list)
    first_detection_request: PositiveInt | None = None

    @model_validator(mode="after")
    def validate_fault_evaluation(self) -> Self:
        for name, values in (
            ("triggered_case_ids", self.triggered_case_ids),
            ("eligible_triggered_case_ids", self.eligible_triggered_case_ids),
            ("detected_case_ids", self.detected_case_ids),
            ("errored_case_ids", self.errored_case_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique case IDs")
        triggered = set(self.triggered_case_ids)
        eligible = set(self.eligible_triggered_case_ids)
        if not eligible <= triggered:
            raise ValueError("eligible triggered cases must be a subset of triggered cases")
        if not set(self.detected_case_ids) <= eligible:
            raise ValueError("detected cases must be a subset of eligible triggered cases")
        if not set(self.errored_case_ids) <= eligible:
            raise ValueError("errored cases must be a subset of eligible triggered cases")

        if self.outcome is FaultEvaluationOutcome.NOT_TRIGGERED:
            if self.trigger_count != 0 or triggered:
                raise ValueError("not_triggered faults cannot contain trigger evidence")
        elif self.trigger_count == 0 or not triggered:
            raise ValueError("triggered fault outcomes require trigger evidence")

        if self.outcome is FaultEvaluationOutcome.DETECTED:
            if not self.detected_case_ids or self.first_detection_request is None:
                raise ValueError("detected faults require detected cases and request position")
            if self.first_detection_request > self.request_count:
                raise ValueError("first detection request cannot exceed request_count")
        elif self.detected_case_ids or self.first_detection_request is not None:
            raise ValueError("only detected faults may contain detection evidence")

        if self.outcome is FaultEvaluationOutcome.NO_ELIGIBLE_CASE and eligible:
            raise ValueError("no_eligible_case cannot contain eligible triggered cases")
        if (
            self.outcome
            in {
                FaultEvaluationOutcome.NOT_DETECTED,
                FaultEvaluationOutcome.INCONCLUSIVE,
            }
            and not eligible
        ):
            raise ValueError(f"{self.outcome.value} requires an eligible triggered case")
        if self.outcome is FaultEvaluationOutcome.INCONCLUSIVE and not self.errored_case_ids:
            raise ValueError("inconclusive faults require an errored eligible case")
        return self


class FaultSummaryMetrics(ContractModel):
    configured_fault_count: NonNegativeInt
    triggered_fault_count: NonNegativeInt
    evaluable_fault_count: NonNegativeInt
    detected_fault_count: NonNegativeInt
    not_detected_fault_count: NonNegativeInt
    not_triggered_fault_count: NonNegativeInt
    no_eligible_case_fault_count: NonNegativeInt
    inconclusive_fault_count: NonNegativeInt
    fault_detection_rate: Rate | None
    faults_detected_per_100_requests: NonNegativeFloat

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if self.configured_fault_count != (
            self.detected_fault_count
            + self.not_detected_fault_count
            + self.not_triggered_fault_count
            + self.no_eligible_case_fault_count
            + self.inconclusive_fault_count
        ):
            raise ValueError("fault outcome counts must equal configured_fault_count")
        if self.triggered_fault_count != (
            self.configured_fault_count - self.not_triggered_fault_count
        ):
            raise ValueError("triggered_fault_count does not match fault outcomes")
        if self.evaluable_fault_count != (
            self.detected_fault_count + self.not_detected_fault_count
        ):
            raise ValueError("evaluable faults must equal detected plus not detected faults")
        expected_rate = (
            self.detected_fault_count / self.evaluable_fault_count
            if self.evaluable_fault_count
            else None
        )
        if not _optional_rate_matches(self.fault_detection_rate, expected_rate):
            raise ValueError("fault_detection_rate does not match fault counts")
        return self


class EvaluationResult(ContractModel):
    schema_version: Literal["1.0"]
    kind: Literal["EvaluationResult"]
    evaluation_id: Identifier
    suite_id: Identifier
    repetition: PositiveInt
    spec_id: str = Field(min_length=1)
    generator: GeneratorMetadata
    admission: CaseAdmissionMetrics
    execution: SuiteExecutionMetrics
    fault_summary: FaultSummaryMetrics
    clean_run_id: Identifier
    faults: list[FaultEvaluation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        fault_ids = [fault.fault_id for fault in self.faults]
        if len(fault_ids) != len(set(fault_ids)):
            raise ValueError("fault IDs must be unique within an evaluation result")
        run_ids = [self.clean_run_id, *(fault.run_id for fault in self.faults)]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("run IDs must be unique within an evaluation result")
        if len(self.faults) != self.fault_summary.configured_fault_count:
            raise ValueError("per-fault results must match configured_fault_count")
        if self.admission.admitted_case_count != self.execution.admitted_case_count:
            raise ValueError("admission and execution case counts must match")
        outcome_counts = {
            outcome: sum(fault.outcome is outcome for fault in self.faults)
            for outcome in FaultEvaluationOutcome
        }
        expected_counts = {
            FaultEvaluationOutcome.DETECTED: self.fault_summary.detected_fault_count,
            FaultEvaluationOutcome.NOT_DETECTED: self.fault_summary.not_detected_fault_count,
            FaultEvaluationOutcome.NOT_TRIGGERED: self.fault_summary.not_triggered_fault_count,
            FaultEvaluationOutcome.NO_ELIGIBLE_CASE: (
                self.fault_summary.no_eligible_case_fault_count
            ),
            FaultEvaluationOutcome.INCONCLUSIVE: self.fault_summary.inconclusive_fault_count,
        }
        if outcome_counts != expected_counts:
            raise ValueError("fault summary counts do not match per-fault outcomes")
        expected_efficiency = (
            self.fault_summary.detected_fault_count * 100 / self.execution.fault_request_count
            if self.execution.fault_request_count
            else 0
        )
        if abs(self.fault_summary.faults_detected_per_100_requests - expected_efficiency) > 1e-12:
            raise ValueError("fault detection efficiency does not match requests")
        return self


def _optional_rate_matches(actual: float | None, expected: float | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return abs(actual - expected) <= 1e-12


__all__ = [
    "CaseAdmissionMetrics",
    "EvaluationResult",
    "FaultEvaluation",
    "FaultEvaluationOutcome",
    "FaultSummaryMetrics",
    "GeneratorKind",
    "GeneratorMetadata",
    "SuiteExecutionMetrics",
]
