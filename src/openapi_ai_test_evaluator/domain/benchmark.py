"""Strict configuration contract for reproducible multi-suite benchmarks."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AnyHttpUrl, Field, model_validator

from openapi_ai_test_evaluator.domain.contracts import ContractModel, Identifier

PositiveInt = Annotated[int, Field(ge=1)]
PathText = Annotated[str, Field(min_length=1)]


class BenchmarkSuiteArm(StrEnum):
    """Whether a suite contains only native cases or shared enhancements too."""

    NATIVE = "native"
    ENHANCED = "enhanced"


class BenchmarkEndpoints(ContractModel):
    """Already-running services used by every configured suite."""

    runner_base_url: AnyHttpUrl
    proxy_control_url: AnyHttpUrl
    sut_reset_url: AnyHttpUrl


class BenchmarkExecutionConfig(ContractModel):
    """Execution policy shared across suites and repetitions."""

    timeout_ms: PositiveInt = 5000
    allow_mutations: bool = False


class BenchmarkRepetitionInput(ContractModel):
    """Frozen artifacts for one suite repetition."""

    repetition: PositiveInt
    cases: PathText
    source_record: PathText
    composition_record: PathText | None = None


class BenchmarkSuiteConfig(ContractModel):
    """One native or enhanced experiment arm."""

    suite_id: Identifier
    arm: BenchmarkSuiteArm
    inputs: list[BenchmarkRepetitionInput] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_inputs(self) -> Self:
        repetitions = [item.repetition for item in self.inputs]
        if len(repetitions) != len(set(repetitions)):
            raise ValueError("suite input repetitions must be unique")
        has_composition = [item.composition_record is not None for item in self.inputs]
        if self.arm is BenchmarkSuiteArm.NATIVE and any(has_composition):
            raise ValueError("native suites cannot contain composition records")
        if self.arm is BenchmarkSuiteArm.ENHANCED and not all(has_composition):
            raise ValueError("enhanced suites require a composition record per repetition")
        return self


class BenchmarkReportConfig(ContractModel):
    """Comparison identifiers and destinations produced after all suites finish."""

    comparison_id: Identifier
    json_output: PathText
    markdown_output: PathText


class BenchmarkConfig(ContractModel):
    """One reproducible benchmark matrix over paired suite repetitions."""

    schema_version: Literal["1.0"]
    kind: Literal["BenchmarkConfig"]
    benchmark_id: Identifier
    spec: PathText
    repetitions: list[PositiveInt] = Field(min_length=1)
    fault_ids: list[Identifier] = Field(min_length=1)
    endpoints: BenchmarkEndpoints
    execution: BenchmarkExecutionConfig = Field(default_factory=BenchmarkExecutionConfig)
    suites: list[BenchmarkSuiteConfig] = Field(min_length=2)
    output_directory: PathText
    report: BenchmarkReportConfig

    @model_validator(mode="after")
    def validate_matrix(self) -> Self:
        if len(self.repetitions) != len(set(self.repetitions)):
            raise ValueError("benchmark repetitions must be unique")
        if self.repetitions != sorted(self.repetitions):
            raise ValueError("benchmark repetitions must be sorted")
        if len(self.fault_ids) != len(set(self.fault_ids)):
            raise ValueError("benchmark fault IDs must be unique")
        suite_ids = [suite.suite_id for suite in self.suites]
        if len(suite_ids) != len(set(suite_ids)):
            raise ValueError("benchmark suite IDs must be unique")
        expected_repetitions = set(self.repetitions)
        for suite in self.suites:
            actual_repetitions = {item.repetition for item in suite.inputs}
            if actual_repetitions != expected_repetitions:
                raise ValueError(
                    f"suite {suite.suite_id!r} inputs must match benchmark repetitions"
                )
        return self


__all__ = [
    "BenchmarkConfig",
    "BenchmarkEndpoints",
    "BenchmarkExecutionConfig",
    "BenchmarkRepetitionInput",
    "BenchmarkReportConfig",
    "BenchmarkSuiteArm",
    "BenchmarkSuiteConfig",
]
