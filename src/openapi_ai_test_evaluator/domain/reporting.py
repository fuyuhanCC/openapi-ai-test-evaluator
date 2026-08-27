"""Strict contracts for multi-suite comparison reports."""

from __future__ import annotations

from enum import StrEnum
from math import fsum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from openapi_ai_test_evaluator.domain.contracts import ContractModel, Identifier
from openapi_ai_test_evaluator.domain.evaluation import GeneratorKind

NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]
NonNegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Rate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class ComparisonMode(StrEnum):
    NATIVE_SUITE = "native_suite"
    AUGMENTED_SUITE = "augmented_suite"
    MIXED_SUITE = "mixed_suite"


class ComparedGenerator(ContractModel):
    kind: GeneratorKind
    name: str = Field(min_length=1)
    version: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)


class MetricStatistics(ContractModel):
    values: list[NonNegativeFloat] = Field(default_factory=list)
    sample_count: NonNegativeInt
    missing_count: NonNegativeInt
    mean: NonNegativeFloat | None
    stddev: NonNegativeFloat | None
    minimum: NonNegativeFloat | None
    maximum: NonNegativeFloat | None

    @model_validator(mode="after")
    def validate_statistics(self) -> Self:
        if self.sample_count != len(self.values):
            raise ValueError("sample_count must equal the number of metric values")
        statistics = (self.mean, self.stddev, self.minimum, self.maximum)
        if not self.values:
            if any(value is not None for value in statistics):
                raise ValueError("metrics without samples cannot contain statistics")
            return self
        if any(value is None for value in statistics):
            raise ValueError("metrics with samples require complete statistics")
        assert self.mean is not None
        assert self.stddev is not None
        assert self.minimum is not None
        assert self.maximum is not None
        expected_mean = fsum(self.values) / len(self.values)
        expected_variance = fsum((value - expected_mean) ** 2 for value in self.values) / len(
            self.values
        )
        expected_stddev = expected_variance**0.5
        expected = (expected_mean, expected_stddev, min(self.values), max(self.values))
        actual = (self.mean, self.stddev, self.minimum, self.maximum)
        if any(abs(left - right) > 1e-12 for left, right in zip(actual, expected, strict=True)):
            raise ValueError("stored metric statistics do not match their values")
        return self


class FaultStability(ContractModel):
    fault_id: Identifier
    repetition_count: PositiveInt
    detected_count: NonNegativeInt
    not_detected_count: NonNegativeInt
    not_triggered_count: NonNegativeInt
    no_eligible_case_count: NonNegativeInt
    inconclusive_count: NonNegativeInt
    evaluable_count: NonNegativeInt
    detection_rate: Rate | None
    first_detection_request: MetricStatistics

    @model_validator(mode="after")
    def validate_fault_counts(self) -> Self:
        if self.repetition_count != (
            self.detected_count
            + self.not_detected_count
            + self.not_triggered_count
            + self.no_eligible_case_count
            + self.inconclusive_count
        ):
            raise ValueError("per-fault outcomes must equal repetition_count")
        if self.evaluable_count != self.detected_count + self.not_detected_count:
            raise ValueError("evaluable_count must equal detected plus not detected")
        expected_rate = self.detected_count / self.evaluable_count if self.evaluable_count else None
        if not _optional_matches(self.detection_rate, expected_rate):
            raise ValueError("fault detection_rate does not match outcome counts")
        if self.first_detection_request.sample_count != self.detected_count:
            raise ValueError("first-detection samples must match detected_count")
        if (
            self.first_detection_request.sample_count + self.first_detection_request.missing_count
            != self.repetition_count
        ):
            raise ValueError("first-detection sample and missing counts must cover repetitions")
        return self


class SuiteComparison(ContractModel):
    suite_id: Identifier
    generator: ComparedGenerator
    repetitions: list[PositiveInt] = Field(min_length=1)
    evaluation_ids: list[Identifier] = Field(min_length=1)
    received_case_count: MetricStatistics
    admitted_case_count: MetricStatistics
    enhancement_case_count: MetricStatistics
    executed_case_count: MetricStatistics
    admission_rate: MetricStatistics
    executable_case_rate: MetricStatistics
    clean_false_positive_rate: MetricStatistics
    operation_coverage_rate: MetricStatistics
    fault_detection_rate: MetricStatistics
    faults_detected_per_100_requests: MetricStatistics
    clean_request_count: MetricStatistics
    fault_request_count: MetricStatistics
    total_request_count: MetricStatistics
    generation_request_count: MetricStatistics
    generation_duration_ms: MetricStatistics
    execution_duration_ms: MetricStatistics
    input_tokens: MetricStatistics
    output_tokens: MetricStatistics
    total_tokens: MetricStatistics
    cached_input_tokens: MetricStatistics
    reasoning_tokens: MetricStatistics
    estimated_cost_usd: MetricStatistics
    faults: list[FaultStability] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_suite(self) -> Self:
        if len(self.repetitions) != len(set(self.repetitions)):
            raise ValueError("suite repetitions must be unique")
        if len(self.evaluation_ids) != len(self.repetitions):
            raise ValueError("one evaluation ID is required per repetition")
        if len(self.evaluation_ids) != len(set(self.evaluation_ids)):
            raise ValueError("suite evaluation IDs must be unique")
        expected_total = len(self.repetitions)
        for name in _SUITE_METRIC_FIELDS:
            metric = getattr(self, name)
            if metric.sample_count + metric.missing_count != expected_total:
                raise ValueError(f"{name} samples must cover every repetition")
        for name in _RATE_METRIC_FIELDS:
            metric = getattr(self, name)
            if any(value > 1 for value in metric.values):
                raise ValueError(f"{name} values cannot exceed one")
        fault_ids = [fault.fault_id for fault in self.faults]
        if len(fault_ids) != len(set(fault_ids)):
            raise ValueError("fault IDs must be unique within a suite comparison")
        if any(fault.repetition_count != expected_total for fault in self.faults):
            raise ValueError("fault repetition counts must match the suite")
        return self


class ComparisonResult(ContractModel):
    schema_version: Literal["1.0"]
    kind: Literal["ComparisonResult"]
    comparison_id: Identifier
    mode: ComparisonMode
    spec_id: str = Field(min_length=1)
    repetitions: list[PositiveInt] = Field(min_length=1)
    fault_ids: list[Identifier] = Field(default_factory=list)
    suites: list[SuiteComparison] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        if len(self.repetitions) != len(set(self.repetitions)):
            raise ValueError("comparison repetitions must be unique")
        if len(self.fault_ids) != len(set(self.fault_ids)):
            raise ValueError("comparison fault IDs must be unique")
        suite_ids = [suite.suite_id for suite in self.suites]
        if len(suite_ids) != len(set(suite_ids)):
            raise ValueError("comparison suite IDs must be unique")
        evaluation_ids = [
            evaluation_id for suite in self.suites for evaluation_id in suite.evaluation_ids
        ]
        if len(evaluation_ids) != len(set(evaluation_ids)):
            raise ValueError("evaluation IDs must be unique across compared suites")
        expected_repetitions = set(self.repetitions)
        expected_faults = set(self.fault_ids)
        for suite in self.suites:
            if set(suite.repetitions) != expected_repetitions:
                raise ValueError("all suites must contain the same paired repetitions")
            if {fault.fault_id for fault in suite.faults} != expected_faults:
                raise ValueError("all suites must contain the same fault set")
        return self


_SUITE_METRIC_FIELDS = (
    "received_case_count",
    "admitted_case_count",
    "enhancement_case_count",
    "executed_case_count",
    "admission_rate",
    "executable_case_rate",
    "clean_false_positive_rate",
    "operation_coverage_rate",
    "fault_detection_rate",
    "faults_detected_per_100_requests",
    "clean_request_count",
    "fault_request_count",
    "total_request_count",
    "generation_request_count",
    "generation_duration_ms",
    "execution_duration_ms",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
    "estimated_cost_usd",
)

_RATE_METRIC_FIELDS = (
    "admission_rate",
    "executable_case_rate",
    "clean_false_positive_rate",
    "operation_coverage_rate",
    "fault_detection_rate",
)


def _optional_matches(actual: float | None, expected: float | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return abs(actual - expected) <= 1e-12


__all__ = [
    "ComparedGenerator",
    "ComparisonMode",
    "ComparisonResult",
    "FaultStability",
    "MetricStatistics",
    "SuiteComparison",
]
