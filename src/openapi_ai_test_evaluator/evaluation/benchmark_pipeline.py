"""Execute every frozen suite in a strict BenchmarkConfig matrix."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from openapi_ai_test_evaluator.domain.benchmark import BenchmarkConfig
from openapi_ai_test_evaluator.domain.composition import SuiteCompositionRecord
from openapi_ai_test_evaluator.domain.evaluation import EvaluationResult
from openapi_ai_test_evaluator.domain.generation import AdaptationRecord, GenerationRecord
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.domain.reporting import ComparisonResult
from openapi_ai_test_evaluator.domain.test_case import TestCaseBatch
from openapi_ai_test_evaluator.evaluation.input_artifacts import (
    load_composition_record,
    load_source_record,
)
from openapi_ai_test_evaluator.evaluation.suite_evaluator import (
    EvaluationInputError,
    validate_composed_suite_case_counts,
    validate_source_record_case_count,
)
from openapi_ai_test_evaluator.evaluation.suite_pipeline import (
    EvaluatedSuiteArtifactPaths,
    run_evaluated_suite,
    write_evaluated_suite_artifacts,
)
from openapi_ai_test_evaluator.generation.batch_composer import case_batch_sha256
from openapi_ai_test_evaluator.reporting import (
    compare_evaluations,
    render_comparison_markdown,
)
from openapi_ai_test_evaluator.spec import load_openapi
from openapi_ai_test_evaluator.validation import (
    load_test_case_batch,
    validate_test_case_batch_semantics,
)


class BenchmarkRunError(RuntimeError):
    """A configured benchmark cannot be prepared, executed, or persisted."""

    def __init__(
        self,
        stage: str,
        message: str,
        *,
        suite_id: str | None = None,
        repetition: int | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.suite_id = suite_id
        self.repetition = repetition


@dataclass(frozen=True, slots=True)
class PreparedBenchmarkRun:
    """All validated inputs for one suite repetition, before HTTP execution."""

    suite_id: str
    repetition: int
    batch: TestCaseBatch
    source_record: GenerationRecord | AdaptationRecord
    composition_record: SuiteCompositionRecord | None
    input_paths: tuple[Path, ...]
    output_directory: Path


@dataclass(frozen=True, slots=True)
class BenchmarkSuiteArtifact:
    """One suite repetition and the raw/evaluation paths it produced."""

    suite_id: str
    repetition: int
    paths: EvaluatedSuiteArtifactPaths


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Completed suite evaluations and their comparison artifacts."""

    benchmark_id: str
    evaluations: tuple[EvaluationResult, ...]
    suites: tuple[BenchmarkSuiteArtifact, ...]
    comparison: ComparisonResult
    comparison_json: Path
    comparison_markdown: Path


def run_benchmark_config(
    config: BenchmarkConfig,
    config_path: Path,
    *,
    overwrite: bool = False,
    execution_transport: httpx.BaseTransport | None = None,
    control_transport: httpx.BaseTransport | None = None,
) -> BenchmarkResult:
    """Preflight, execute, evaluate, and compare a complete benchmark matrix."""
    base_directory = config_path.parent.resolve()
    spec_path = _resolve_path(base_directory, config.spec)
    output_directory = _resolve_path(base_directory, config.output_directory)
    comparison_json = _resolve_path(base_directory, config.report.json_output)
    comparison_markdown = _resolve_path(base_directory, config.report.markdown_output)
    prepared, spec = _prepare_runs(config, base_directory, spec_path, output_directory)
    _prepare_outputs(
        config_path,
        prepared,
        spec_path,
        output_directory,
        comparison_json,
        comparison_markdown,
        overwrite=overwrite,
    )

    evaluations: list[EvaluationResult] = []
    suite_artifacts: list[BenchmarkSuiteArtifact] = []
    for item in prepared:
        try:
            evaluated = run_evaluated_suite(
                item.batch,
                spec,
                item.source_record,
                suite_id=item.suite_id,
                repetition=item.repetition,
                evaluation_id=f"evaluation-{item.suite_id}-r{item.repetition}",
                runner_base_url=str(config.endpoints.runner_base_url),
                proxy_control_url=str(config.endpoints.proxy_control_url),
                sut_reset_url=str(config.endpoints.sut_reset_url),
                fault_ids=list(config.fault_ids),
                composition_record=item.composition_record,
                timeout_ms=config.execution.timeout_ms,
                allow_mutations=config.execution.allow_mutations,
                execution_transport=execution_transport,
                control_transport=control_transport,
            )
            paths = write_evaluated_suite_artifacts(
                evaluated,
                item.output_directory,
                overwrite=overwrite,
            )
        except (ValueError, RuntimeError, OSError, httpx.HTTPError) as error:
            raise BenchmarkRunError(
                "suite-execution",
                str(error),
                suite_id=item.suite_id,
                repetition=item.repetition,
            ) from error
        evaluations.append(evaluated.evaluation)
        suite_artifacts.append(
            BenchmarkSuiteArtifact(
                suite_id=item.suite_id,
                repetition=item.repetition,
                paths=paths,
            )
        )

    try:
        comparison = compare_evaluations(
            evaluations,
            comparison_id=config.report.comparison_id,
        )
        _write_report(comparison_json, comparison.model_dump_json(indent=2))
        _write_report(comparison_markdown, render_comparison_markdown(comparison))
    except (ValueError, RuntimeError, OSError) as error:
        raise BenchmarkRunError("comparison", str(error)) from error
    return BenchmarkResult(
        benchmark_id=config.benchmark_id,
        evaluations=tuple(evaluations),
        suites=tuple(suite_artifacts),
        comparison=comparison,
        comparison_json=comparison_json,
        comparison_markdown=comparison_markdown,
    )


def _prepare_runs(
    config: BenchmarkConfig,
    base_directory: Path,
    spec_path: Path,
    output_directory: Path,
) -> tuple[list[PreparedBenchmarkRun], OpenAPISpec]:
    try:
        spec = load_openapi(spec_path)
    except (ValueError, OSError) as error:
        raise BenchmarkRunError("openapi", str(error)) from error

    prepared: list[PreparedBenchmarkRun] = []
    for suite in config.suites:
        for item in sorted(suite.inputs, key=lambda value: value.repetition):
            cases_path = _resolve_path(base_directory, item.cases)
            source_record_path = _resolve_path(base_directory, item.source_record)
            composition_record_path = (
                _resolve_path(base_directory, item.composition_record)
                if item.composition_record is not None
                else None
            )
            try:
                batch = load_test_case_batch(cases_path)
                issues = validate_test_case_batch_semantics(batch, spec)
                if issues:
                    codes = ", ".join(sorted({issue.code for issue in issues}))
                    raise ValueError(f"case semantics are invalid: {codes}")
                source_record = load_source_record(source_record_path)
                composition_record = (
                    load_composition_record(composition_record_path)
                    if composition_record_path is not None
                    else None
                )
                _validate_frozen_input(batch, source_record, composition_record)
            except (ValueError, OSError) as error:
                raise BenchmarkRunError(
                    "suite-input",
                    str(error),
                    suite_id=suite.suite_id,
                    repetition=item.repetition,
                ) from error
            prepared.append(
                PreparedBenchmarkRun(
                    suite_id=suite.suite_id,
                    repetition=item.repetition,
                    batch=batch,
                    source_record=source_record,
                    composition_record=composition_record,
                    input_paths=(
                        cases_path,
                        source_record_path,
                        *((composition_record_path,) if composition_record_path else ()),
                    ),
                    output_directory=(
                        output_directory / suite.suite_id / f"r{item.repetition}"
                    ),
                )
            )
    return prepared, spec


def _validate_frozen_input(
    batch: TestCaseBatch,
    source_record: GenerationRecord | AdaptationRecord,
    composition_record: SuiteCompositionRecord | None,
) -> None:
    if composition_record is None:
        validate_source_record_case_count(source_record, len(batch.cases))
        return
    validate_composed_suite_case_counts(
        source_record,
        composition_record,
        len(batch.cases),
    )
    if case_batch_sha256(batch) != composition_record.composed_batch.sha256:
        raise EvaluationInputError(
            "frozen batch content does not match the composition record hash"
        )


def _prepare_outputs(
    config_path: Path,
    prepared: list[PreparedBenchmarkRun],
    spec_path: Path,
    output_directory: Path,
    comparison_json: Path,
    comparison_markdown: Path,
    *,
    overwrite: bool,
) -> None:
    if comparison_json == comparison_markdown:
        raise BenchmarkRunError("artifacts", "comparison output paths must be different")
    input_paths = {
        config_path.resolve(),
        spec_path,
        *(path for item in prepared for path in item.input_paths),
    }
    if any(path.is_relative_to(output_directory) for path in input_paths):
        raise BenchmarkRunError(
            "artifacts",
            "benchmark output directory cannot contain benchmark inputs",
        )
    report_paths = (comparison_json, comparison_markdown)
    if any(path in input_paths for path in report_paths):
        raise BenchmarkRunError("artifacts", "report outputs cannot overwrite benchmark inputs")
    existing = [
        path
        for path in (
            *(item.output_directory for item in prepared),
            *report_paths,
        )
        if path.exists()
    ]
    if existing and not overwrite:
        joined = ", ".join(str(path) for path in existing)
        raise BenchmarkRunError("artifacts", f"refusing to overwrite artifacts: {joined}")


def _resolve_path(base_directory: Path, configured: str) -> Path:
    path = Path(configured)
    if path.is_absolute():
        return path.resolve()
    return (base_directory / path).resolve()


def _write_report(path: Path, serialized: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized.rstrip("\n") + "\n", encoding="utf-8")


__all__ = [
    "BenchmarkResult",
    "BenchmarkRunError",
    "BenchmarkSuiteArtifact",
    "PreparedBenchmarkRun",
    "run_benchmark_config",
]
