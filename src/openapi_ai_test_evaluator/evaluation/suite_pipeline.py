"""Connect one frozen suite execution to evaluation and artifact persistence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from openapi_ai_test_evaluator.domain.composition import SuiteCompositionRecord
from openapi_ai_test_evaluator.domain.evaluation import EvaluationResult
from openapi_ai_test_evaluator.domain.generation import AdaptationRecord, GenerationRecord
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.domain.pricing import TokenPricingSnapshot
from openapi_ai_test_evaluator.domain.test_case import TestCaseBatch
from openapi_ai_test_evaluator.evaluation.suite_evaluator import (
    EvaluationInputError,
    evaluate_suite_execution,
    validate_composed_suite_case_counts,
    validate_source_record_case_count,
    validate_source_record_pricing,
)
from openapi_ai_test_evaluator.evaluation.suite_runner import (
    SuiteExecution,
    execute_fault_suite,
)
from openapi_ai_test_evaluator.generation.batch_composer import case_batch_sha256


class SuiteArtifactError(RuntimeError):
    """One evaluated-suite artifact set cannot be written safely."""


@dataclass(frozen=True, slots=True)
class EvaluatedSuite:
    """Raw runs and their derived evaluation for one suite repetition."""

    execution: SuiteExecution
    evaluation: EvaluationResult

    def __post_init__(self) -> None:
        if self.execution.suite_id != self.evaluation.suite_id:
            raise ValueError("execution and evaluation suite IDs must match")
        if self.execution.repetition != self.evaluation.repetition:
            raise ValueError("execution and evaluation repetitions must match")
        if self.execution.clean.run_id != self.evaluation.clean_run_id:
            raise ValueError("evaluation must reference the execution clean run")
        execution_fault_runs = {
            fault.fault_id: fault.result.run_id for fault in self.execution.faults
        }
        evaluation_fault_runs = {fault.fault_id: fault.run_id for fault in self.evaluation.faults}
        if execution_fault_runs != evaluation_fault_runs:
            raise ValueError("evaluation must reference every execution fault run")


@dataclass(frozen=True, slots=True)
class EvaluatedSuiteArtifactPaths:
    """Paths written for one evaluated suite repetition."""

    clean_run: Path
    fault_runs: tuple[Path, ...]
    evaluation: Path


def run_evaluated_suite(
    batch: TestCaseBatch,
    spec: OpenAPISpec,
    source_record: GenerationRecord | AdaptationRecord,
    *,
    suite_id: str,
    repetition: int,
    evaluation_id: str,
    runner_base_url: str,
    proxy_control_url: str,
    sut_reset_url: str,
    fault_ids: list[str],
    composition_record: SuiteCompositionRecord | None = None,
    pricing: TokenPricingSnapshot | None = None,
    timeout_ms: int = 5000,
    allow_mutations: bool = False,
    execution_transport: httpx.BaseTransport | None = None,
    control_transport: httpx.BaseTransport | None = None,
) -> EvaluatedSuite:
    """Execute and evaluate one already-generated, unchanged test suite."""
    validate_source_record_pricing(source_record, pricing)
    if composition_record is None:
        validate_source_record_case_count(source_record, len(batch.cases))
    else:
        validate_composed_suite_case_counts(
            source_record,
            composition_record,
            len(batch.cases),
        )
        if case_batch_sha256(batch) != composition_record.composed_batch.sha256:
            raise EvaluationInputError(
                "frozen batch content does not match the composition record hash"
            )
    execution = execute_fault_suite(
        batch,
        spec,
        suite_id=suite_id,
        repetition=repetition,
        runner_base_url=runner_base_url,
        proxy_control_url=proxy_control_url,
        sut_reset_url=sut_reset_url,
        fault_ids=fault_ids,
        timeout_ms=timeout_ms,
        allow_mutations=allow_mutations,
        execution_transport=execution_transport,
        control_transport=control_transport,
    )
    evaluation = evaluate_suite_execution(
        execution,
        spec,
        source_record,
        evaluation_id=evaluation_id,
        composition_record=composition_record,
        pricing=pricing,
    )
    return EvaluatedSuite(execution=execution, evaluation=evaluation)


def write_evaluated_suite_artifacts(
    suite: EvaluatedSuite,
    output_directory: Path,
    *,
    overwrite: bool = False,
) -> EvaluatedSuiteArtifactPaths:
    """Persist raw runs and EvaluationResult as separate strict JSON files."""
    clean_path = output_directory / "execution" / "clean.json"
    fault_paths = tuple(
        output_directory / "execution" / "faults" / f"{fault.fault_id}.json"
        for fault in suite.execution.faults
    )
    evaluation_path = output_directory / "evaluation" / "evaluation.json"
    paths = (clean_path, *fault_paths, evaluation_path)
    existing = [path for path in paths if path.exists()]
    if existing and not overwrite:
        existing_text = ", ".join(str(path) for path in existing)
        raise SuiteArtifactError(
            f"refusing to overwrite existing evaluated-suite artifacts: {existing_text}"
        )

    documents = (
        suite.execution.clean.model_dump_json(indent=2),
        *(fault.result.model_dump_json(indent=2) for fault in suite.execution.faults),
        suite.evaluation.model_dump_json(indent=2),
    )
    try:
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
        for path, document in zip(paths, documents, strict=True):
            path.write_text(document + "\n", encoding="utf-8")
    except OSError as error:
        raise SuiteArtifactError(
            f"cannot write evaluated-suite artifacts under {output_directory}: {error}"
        ) from error
    return EvaluatedSuiteArtifactPaths(
        clean_run=clean_path,
        fault_runs=fault_paths,
        evaluation=evaluation_path,
    )


__all__ = [
    "EvaluatedSuite",
    "EvaluatedSuiteArtifactPaths",
    "SuiteArtifactError",
    "run_evaluated_suite",
    "write_evaluated_suite_artifacts",
]
