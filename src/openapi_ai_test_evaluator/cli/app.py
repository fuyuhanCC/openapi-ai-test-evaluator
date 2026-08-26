"""OATE command-line application."""

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn
from uuid import uuid4

import schemathesis
import typer
from hypothesis.errors import HypothesisException
from pydantic import ValidationError
from schemathesis.errors import SchemathesisError

from openapi_ai_test_evaluator.domain import (
    GenerationConfig,
    GenerationRecord,
    OpenAPISpec,
    TestCaseBatch,
    TestPlan,
)
from openapi_ai_test_evaluator.domain.execution import ExecutionOutcome
from openapi_ai_test_evaluator.evaluation import (
    BenchmarkControlError,
    EvaluationInputError,
    SourceRecordLoadError,
    SuiteArtifactError,
    load_source_record,
    run_evaluated_suite,
    write_evaluated_suite_artifacts,
)
from openapi_ai_test_evaluator.execution import execute_test_case_batch, execute_test_plan
from openapi_ai_test_evaluator.generation import (
    PROMPT_VERSION,
    DeepSeekProvider,
    DeepSeekProviderConfigError,
    PromptBuildError,
    SchemathesisGenerationConfig,
    generate_cases_from_openapi,
    generate_schemathesis_batch,
)
from openapi_ai_test_evaluator.reporting import (
    ComparisonInputError,
    EvaluationArtifactError,
    compare_evaluations,
    load_evaluation_result,
    render_comparison_markdown,
)
from openapi_ai_test_evaluator.spec import SpecLoadError, load_openapi
from openapi_ai_test_evaluator.validation import (
    PlanLoadError,
    SemanticIssue,
    TestCaseBatchLoadError,
    load_test_case_batch,
    load_test_plan,
    validate_plan_semantics,
    validate_test_case_batch_semantics,
)

app = typer.Typer(
    name="oate",
    help="Generate and evaluate declarative tests for OpenAPI services.",
    no_args_is_help=True,
)
plan_app = typer.Typer(help="Inspect and validate TestPlan documents.", no_args_is_help=True)
cases_app = typer.Typer(
    help="Validate and run provider-independent TestCaseBatch documents.",
    no_args_is_help=True,
)
report_app = typer.Typer(
    help="Compare evaluated generator suites and render reports.",
    no_args_is_help=True,
)
benchmark_app = typer.Typer(
    help="Execute reproducible clean-versus-fault benchmark suites.",
    no_args_is_help=True,
)
app.add_typer(plan_app, name="plan")
app.add_typer(cases_app, name="cases")
app.add_typer(report_app, name="report")
app.add_typer(benchmark_app, name="benchmark")


class GenerationProviderChoice(StrEnum):
    DEEPSEEK = "deepseek"


class BaselineToolChoice(StrEnum):
    SCHEMATHESIS = "schemathesis"


@benchmark_app.command("run-suite")
def run_benchmark_suite(
    spec: Annotated[
        Path,
        typer.Option("--spec", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    cases: Annotated[
        Path,
        typer.Option("--cases", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    source_record: Annotated[
        Path,
        typer.Option(
            "--source-record",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="GenerationRecord or AdaptationRecord JSON for these cases.",
        ),
    ],
    suite_id: Annotated[
        str,
        typer.Option("--suite-id", help="Stable generator-suite identifier."),
    ],
    runner_base_url: Annotated[
        str,
        typer.Option("--runner-base-url", help="Fault-proxy URL used for measured traffic."),
    ],
    proxy_control_url: Annotated[
        str,
        typer.Option("--proxy-control-url", help="Fault-proxy control URL."),
    ],
    sut_reset_url: Annotated[
        str,
        typer.Option("--sut-reset-url", help="Direct SUT reset endpoint outside measured traffic."),
    ],
    fault_ids: Annotated[
        list[str],
        typer.Option("--fault", help="Fault ID to execute; repeat for every configured fault."),
    ],
    output_directory: Annotated[
        Path,
        typer.Option("--output-directory", file_okay=False, dir_okay=True),
    ],
    repetition: Annotated[
        int,
        typer.Option("--repetition", min=1, help="One-based paired repetition number."),
    ] = 1,
    evaluation_id: Annotated[
        str | None,
        typer.Option("--evaluation-id", help="Defaults to evaluation-<suite>-r<repetition>."),
    ] = None,
    timeout_ms: Annotated[
        int,
        typer.Option("--timeout-ms", min=1, help="Control and per-request timeout."),
    ] = 5000,
    allow_mutations: Annotated[
        bool,
        typer.Option(
            "--allow-mutations",
            help="Confirm that mutating cases target an isolated benchmark environment.",
        ),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Allow replacing an existing suite artifact set."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable command summary."),
    ] = False,
) -> None:
    """Run and evaluate one frozen generator suite in one repetition."""
    batch, openapi_spec = _load_validated_case_inputs(cases, spec, json_output)
    try:
        record = load_source_record(source_record)
    except SourceRecordLoadError as error:
        _report_benchmark_error("source-record", error, json_output)

    _prepare_benchmark_output(
        output_directory,
        [spec, cases, source_record],
        overwrite,
        json_output,
    )
    actual_evaluation_id = evaluation_id or f"evaluation-{suite_id}-r{repetition}"
    try:
        evaluated = run_evaluated_suite(
            batch,
            openapi_spec,
            record,
            suite_id=suite_id,
            repetition=repetition,
            evaluation_id=actual_evaluation_id,
            runner_base_url=runner_base_url,
            proxy_control_url=proxy_control_url,
            sut_reset_url=sut_reset_url,
            fault_ids=fault_ids,
            timeout_ms=timeout_ms,
            allow_mutations=allow_mutations,
        )
    except (BenchmarkControlError, EvaluationInputError, ValidationError, ValueError) as error:
        _report_benchmark_error("execution", error, json_output)

    try:
        paths = write_evaluated_suite_artifacts(
            evaluated,
            output_directory,
            overwrite=overwrite,
        )
    except SuiteArtifactError as error:
        _report_benchmark_error("artifacts", error, json_output)

    summary = {
        "status": "completed",
        "evaluation_id": evaluated.evaluation.evaluation_id,
        "suite_id": evaluated.evaluation.suite_id,
        "repetition": evaluated.evaluation.repetition,
        "clean_outcome": evaluated.execution.clean.outcome.value,
        "configured_faults": evaluated.evaluation.fault_summary.configured_fault_count,
        "detected_faults": evaluated.evaluation.fault_summary.detected_fault_count,
        "output_directory": str(output_directory),
        "evaluation_output": str(paths.evaluation),
    }
    if json_output:
        typer.echo(json.dumps(summary))
    else:
        typer.echo(
            f"Evaluation {summary['evaluation_id']}: completed "
            f"({summary['detected_faults']}/{summary['configured_faults']} faults detected); "
            f"wrote {output_directory}"
        )


def _prepare_benchmark_output(
    output_directory: Path,
    input_paths: list[Path],
    overwrite: bool,
    json_output: bool,
) -> None:
    resolved_output = output_directory.resolve()
    if any(path.resolve().is_relative_to(resolved_output) for path in input_paths):
        _report_benchmark_error(
            "artifacts",
            ValueError("benchmark output directory cannot contain an input artifact"),
            json_output,
        )
    if output_directory.exists() and not overwrite:
        _report_benchmark_error(
            "artifacts",
            ValueError(f"refusing to reuse existing output directory: {output_directory}"),
            json_output,
        )


def _report_benchmark_error(
    stage: str,
    error: Exception,
    json_output: bool,
) -> NoReturn:
    if json_output:
        typer.echo(json.dumps({"status": "not_started", "stage": stage, "error": str(error)}))
    else:
        typer.echo(f"Cannot run benchmark suite ({stage}): {error}", err=True)
    raise typer.Exit(code=1)


@report_app.command("compare")
def compare_reports(
    evaluations: Annotated[
        list[Path],
        typer.Option(
            "--evaluation",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="EvaluationResult JSON; repeat once per suite and repetition.",
        ),
    ],
    comparison_id: Annotated[
        str,
        typer.Option("--comparison-id", help="Stable identifier for this comparison."),
    ],
    json_output_path: Annotated[
        Path,
        typer.Option("--json-output", file_okay=True, dir_okay=False),
    ],
    markdown_output_path: Annotated[
        Path,
        typer.Option("--markdown-output", file_okay=True, dir_okay=False),
    ],
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Allow replacing existing report files."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable command summary."),
    ] = False,
) -> None:
    """Aggregate paired EvaluationResult artifacts into JSON and Markdown."""
    try:
        loaded = [load_evaluation_result(path) for path in evaluations]
    except EvaluationArtifactError as error:
        _report_comparison_error("evaluation-artifacts", error, json_output)

    try:
        comparison = compare_evaluations(loaded, comparison_id=comparison_id)
    except (ComparisonInputError, ValidationError) as error:
        _report_comparison_error("comparison", error, json_output)

    _prepare_comparison_outputs(
        evaluations,
        json_output_path,
        markdown_output_path,
        overwrite,
        json_output,
    )
    _write_comparison_artifact(
        json_output_path,
        comparison.model_dump_json(indent=2),
        json_output,
    )
    _write_comparison_artifact(
        markdown_output_path,
        render_comparison_markdown(comparison),
        json_output,
    )
    summary = {
        "status": "succeeded",
        "comparison_id": comparison.comparison_id,
        "suites": len(comparison.suites),
        "repetitions": len(comparison.repetitions),
        "json_output": str(json_output_path),
        "markdown_output": str(markdown_output_path),
    }
    if json_output:
        typer.echo(json.dumps(summary))
    else:
        typer.echo(
            f"Comparison {comparison.comparison_id}: "
            f"{_count_label(len(comparison.suites), 'suite')}, "
            f"{_count_label(len(comparison.repetitions), 'repetition')}; "
            f"wrote {json_output_path}, {markdown_output_path}"
        )


def _prepare_comparison_outputs(
    evaluation_paths: list[Path],
    json_output_path: Path,
    markdown_output_path: Path,
    overwrite: bool,
    json_output: bool,
) -> None:
    outputs = (json_output_path, markdown_output_path)
    resolved_outputs = {path.resolve() for path in outputs}
    if len(resolved_outputs) != len(outputs):
        _report_comparison_error(
            "artifacts",
            ValueError("json-output and markdown-output must be different files"),
            json_output,
        )
    if resolved_outputs & {path.resolve() for path in evaluation_paths}:
        _report_comparison_error(
            "artifacts",
            ValueError("report outputs cannot overwrite source evaluation artifacts"),
            json_output,
        )
    existing = [path for path in outputs if path.exists()]
    if existing and not overwrite:
        paths = ", ".join(str(path) for path in existing)
        _report_comparison_error(
            "artifacts",
            ValueError(f"refusing to overwrite existing report artifacts: {paths}"),
            json_output,
        )
    try:
        for path in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        _report_comparison_error("artifacts", error, json_output)


def _write_comparison_artifact(
    path: Path,
    serialized: str,
    json_output: bool,
) -> None:
    try:
        path.write_text(serialized.rstrip("\n") + "\n", encoding="utf-8")
    except OSError as error:
        _report_comparison_error("artifacts", error, json_output)


def _report_comparison_error(
    stage: str,
    error: Exception,
    json_output: bool,
) -> NoReturn:
    if json_output:
        typer.echo(json.dumps({"status": "not_started", "stage": stage, "error": str(error)}))
    else:
        typer.echo(f"Cannot create comparison report ({stage}): {error}", err=True)
    raise typer.Exit(code=1)


@cases_app.command("generate")
def generate_cases(
    spec: Annotated[
        Path,
        typer.Option("--spec", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    cases_output: Annotated[
        Path,
        typer.Option("--cases-output", file_okay=True, dir_okay=False),
    ],
    record_output: Annotated[
        Path,
        typer.Option("--record-output", file_okay=True, dir_okay=False),
    ],
    raw_output: Annotated[
        Path,
        typer.Option(
            "--raw-output",
            file_okay=True,
            dir_okay=False,
            help="Store the provider's unvalidated output text when a response is received.",
        ),
    ],
    provider: Annotated[
        GenerationProviderChoice,
        typer.Option("--provider", help="Test-case generation provider."),
    ] = GenerationProviderChoice.DEEPSEEK,
    model: Annotated[
        str,
        typer.Option("--model", help="Provider model identifier."),
    ] = "deepseek-v4-flash",
    generation_id: Annotated[
        str | None,
        typer.Option("--generation-id", help="Stable identifier for this generation attempt."),
    ] = None,
    prompt_version: Annotated[
        str,
        typer.Option("--prompt-version", help="Versioned generation prompt."),
    ] = PROMPT_VERSION,
    max_cases: Annotated[
        int,
        typer.Option("--max-cases", help="Maximum generated test cases."),
    ] = 20,
    max_steps_per_case: Annotated[
        int,
        typer.Option("--max-steps-per-case", help="Maximum setup/main/cleanup steps per case."),
    ] = 5,
    temperature: Annotated[
        float,
        typer.Option("--temperature", help="Model sampling temperature."),
    ] = 0.0,
    max_output_tokens: Annotated[
        int,
        typer.Option("--max-output-tokens", help="Maximum generated tokens."),
    ] = 4096,
    timeout_ms: Annotated[
        int,
        typer.Option("--timeout-ms", help="Provider request timeout in milliseconds."),
    ] = 60_000,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Allow replacing existing artifact files."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable generation summary."),
    ] = False,
) -> None:
    """Generate cases while preserving validated, metadata, and raw artifacts."""
    _prepare_generation_outputs(
        cases_output,
        record_output,
        raw_output,
        overwrite,
        json_output,
    )

    actual_generation_id = generation_id or _new_generation_id()
    if re.fullmatch(r"[a-z][a-z0-9-]*", actual_generation_id) is None:
        _report_generation_input_error(
            "config",
            ValueError("generation ID must match ^[a-z][a-z0-9-]*$"),
            json_output,
        )

    try:
        openapi_spec = load_openapi(spec)
    except SpecLoadError as error:
        _report_generation_input_error("openapi", error, json_output)

    try:
        config = GenerationConfig(
            model=model,
            prompt_version=prompt_version,
            max_cases=max_cases,
            max_steps_per_case=max_steps_per_case,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            timeout_ms=timeout_ms,
        )
    except ValidationError as error:
        _report_generation_input_error("config", error, json_output)

    if provider is not GenerationProviderChoice.DEEPSEEK:
        _report_generation_input_error(
            "provider-config",
            ValueError(f"unsupported generation provider: {provider.value}"),
            json_output,
        )

    try:
        deepseek_provider = _deepseek_provider_from_env()
        with deepseek_provider:
            attempt = generate_cases_from_openapi(
                deepseek_provider,
                openapi_spec,
                config,
                generation_id=actual_generation_id,
            )
    except (DeepSeekProviderConfigError, PromptBuildError) as error:
        stage = "provider-config" if isinstance(error, DeepSeekProviderConfigError) else "prompt"
        _report_generation_input_error(stage, error, json_output)

    written_raw_output: Path | None = None
    if attempt.provider_output_text is not None:
        _write_generation_artifact(raw_output, attempt.provider_output_text, json_output)
        written_raw_output = raw_output

    _write_generation_artifact(record_output, attempt.record.model_dump_json(indent=2), json_output)

    if attempt.batch is None:
        summary = _generation_summary(
            attempt.record,
            cases_output=None,
            record_output=record_output,
            raw_output=written_raw_output,
        )
        _emit_generation_summary(summary, json_output)
        raise typer.Exit(code=1)

    _write_generation_artifact(cases_output, attempt.batch.model_dump_json(indent=2), json_output)
    summary = _generation_summary(
        attempt.record,
        cases_output=cases_output,
        record_output=record_output,
        raw_output=written_raw_output,
        case_count=len(attempt.batch.cases),
    )
    _emit_generation_summary(summary, json_output)


def _deepseek_provider_from_env() -> DeepSeekProvider:
    return DeepSeekProvider.from_env()


def _new_generation_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"generation-{timestamp}-{uuid4().hex[:8]}"


def _prepare_generation_outputs(
    cases_output: Path,
    record_output: Path,
    raw_output: Path,
    overwrite: bool,
    json_output: bool,
) -> None:
    outputs = (cases_output, record_output, raw_output)
    if len({path.resolve() for path in outputs}) != len(outputs):
        _report_generation_input_error(
            "artifacts",
            ValueError("cases-output, record-output, and raw-output must be different files"),
            json_output,
        )
    existing = [path for path in outputs if path.exists()]
    if existing and not overwrite:
        paths = ", ".join(str(path) for path in existing)
        _report_generation_input_error(
            "artifacts",
            ValueError(f"refusing to overwrite existing artifacts: {paths}"),
            json_output,
        )
    try:
        for path in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        _report_generation_input_error("artifacts", error, json_output)


def _write_generation_artifact(path: Path, serialized: str, json_output: bool) -> None:
    try:
        path.write_text(f"{serialized}\n", encoding="utf-8")
    except OSError as error:
        _report_generation_input_error("artifacts", error, json_output)


def _generation_summary(
    record: GenerationRecord,
    *,
    cases_output: Path | None,
    record_output: Path,
    raw_output: Path | None,
    case_count: int | None = None,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "generation_id": record.generation_id,
        "status": record.status.value,
        "provider": record.provider,
        "model": record.model,
        "record_output": str(record_output),
    }
    if cases_output is not None:
        summary["cases_output"] = str(cases_output)
    if raw_output is not None:
        summary["raw_output"] = str(raw_output)
    if case_count is not None:
        summary["cases"] = case_count
    if record.error is not None:
        summary["error"] = record.error.model_dump()
    if record.case_admission is not None:
        summary.update(
            {
                "received_cases": record.case_admission.received_case_count,
                "admitted_cases": record.case_admission.admitted_case_count,
                "rejected_cases": record.case_admission.rejected_case_count,
                "case_rejections": [
                    rejection.model_dump() for rejection in record.case_admission.rejections
                ],
            }
        )
    return summary


def _emit_generation_summary(summary: dict[str, object], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(summary))
        return
    status = summary["status"]
    if status == "succeeded":
        destinations = [summary["cases_output"], summary["record_output"]]
        if "raw_output" in summary:
            destinations.append(summary["raw_output"])
        typer.echo(
            f"Generation {summary['generation_id']}: succeeded "
            f"({_count_label(summary['cases'], 'case')}); "
            f"wrote {', '.join(str(path) for path in destinations)}"
        )
    else:
        error = summary.get("error")
        destinations = [summary["record_output"]]
        if "raw_output" in summary:
            destinations.append(summary["raw_output"])
        typer.echo(
            f"Generation {summary['generation_id']}: {status}; "
            f"wrote {', '.join(str(path) for path in destinations)}; error={error}",
            err=True,
        )


def _report_generation_input_error(
    stage: str,
    error: Exception,
    json_output: bool,
) -> NoReturn:
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "status": "not_started",
                    "stage": stage,
                    "error": str(error),
                }
            )
        )
    else:
        typer.echo(f"Cannot generate TestCaseBatch ({stage}): {error}", err=True)
    raise typer.Exit(code=1)


@cases_app.command("generate-baseline")
def generate_baseline_cases(
    spec: Annotated[
        Path,
        typer.Option("--spec", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    cases_output: Annotated[
        Path,
        typer.Option("--cases-output", file_okay=True, dir_okay=False),
    ],
    record_output: Annotated[
        Path,
        typer.Option("--record-output", file_okay=True, dir_okay=False),
    ],
    tool: Annotated[
        BaselineToolChoice,
        typer.Option("--tool", help="Conventional test-case generator."),
    ] = BaselineToolChoice.SCHEMATHESIS,
    include_examples: Annotated[
        bool,
        typer.Option("--examples/--no-examples", help="Include all explicit OpenAPI examples."),
    ] = True,
    include_coverage: Annotated[
        bool,
        typer.Option("--coverage/--no-coverage", help="Include all finite coverage cases."),
    ] = True,
    fuzzing_positive_cases_per_operation: Annotated[
        int,
        typer.Option("--fuzz-pos", help="Positive fuzzing cases per operation."),
    ] = 5,
    fuzzing_negative_cases_per_operation: Annotated[
        int,
        typer.Option("--fuzz-neg", help="Negative fuzzing cases per operation."),
    ] = 5,
    seed: Annotated[
        int,
        typer.Option("--seed", help="Reproducible generator seed."),
    ] = 0,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Allow replacing existing artifact files."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable adaptation summary."),
    ] = False,
) -> None:
    """Generate and adapt conventional baseline cases without executing HTTP requests."""
    _prepare_baseline_outputs(cases_output, record_output, overwrite, json_output)

    try:
        openapi_spec = load_openapi(spec)
    except SpecLoadError as error:
        _report_generation_input_error("openapi", error, json_output)

    try:
        config = SchemathesisGenerationConfig(
            include_examples=include_examples,
            include_coverage=include_coverage,
            fuzzing_positive_cases_per_operation=fuzzing_positive_cases_per_operation,
            fuzzing_negative_cases_per_operation=fuzzing_negative_cases_per_operation,
            seed=seed,
        )
    except ValidationError as error:
        _report_generation_input_error("config", error, json_output)

    if tool is not BaselineToolChoice.SCHEMATHESIS:
        _report_generation_input_error(
            "tool-config",
            ValueError(f"unsupported baseline tool: {tool.value}"),
            json_output,
        )

    try:
        schema = schemathesis.openapi.from_path(spec)
        adaptation = generate_schemathesis_batch(schema, openapi_spec, config)
    except (OSError, SchemathesisError, HypothesisException) as error:
        _report_generation_input_error("schemathesis", error, json_output)

    _write_generation_artifact(
        record_output,
        adaptation.record.model_dump_json(indent=2),
        json_output,
    )
    summary: dict[str, object] = {
        "status": "succeeded" if adaptation.batch is not None else "no_adapted_cases",
        "tool": adaptation.record.tool,
        "received_cases": adaptation.record.received_case_count,
        "adapted_cases": adaptation.record.adapted_case_count,
        "rejected_cases": adaptation.record.rejected_case_count,
        "record_output": str(record_output),
        "skip_reasons": [reason.model_dump() for reason in adaptation.record.skip_reasons],
    }
    if adaptation.batch is None:
        _emit_baseline_summary(summary, json_output)
        raise typer.Exit(code=1)

    _write_generation_artifact(
        cases_output,
        adaptation.batch.model_dump_json(indent=2),
        json_output,
    )
    summary["cases_output"] = str(cases_output)
    _emit_baseline_summary(summary, json_output)


def _prepare_baseline_outputs(
    cases_output: Path,
    record_output: Path,
    overwrite: bool,
    json_output: bool,
) -> None:
    if cases_output.resolve() == record_output.resolve():
        _report_generation_input_error(
            "artifacts",
            ValueError("cases-output and record-output must be different files"),
            json_output,
        )
    existing = [path for path in (cases_output, record_output) if path.exists()]
    if existing and not overwrite:
        paths = ", ".join(str(path) for path in existing)
        _report_generation_input_error(
            "artifacts",
            ValueError(f"refusing to overwrite existing artifacts: {paths}"),
            json_output,
        )
    try:
        cases_output.parent.mkdir(parents=True, exist_ok=True)
        record_output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        _report_generation_input_error("artifacts", error, json_output)


def _emit_baseline_summary(summary: dict[str, object], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(summary))
        return
    typer.echo(
        f"Schemathesis adaptation: {summary['status']} "
        f"({summary['received_cases']} received, {summary['adapted_cases']} adapted, "
        f"{summary['rejected_cases']} rejected); wrote {summary['record_output']}"
        + (f", {summary['cases_output']}" if "cases_output" in summary else ""),
        err=summary["status"] != "succeeded",
    )


@cases_app.command("validate")
def validate_cases(
    cases: Annotated[
        Path,
        typer.Option("--cases", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Also validate cases against a supported OpenAPI 3.0/3.1 document.",
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    """Validate a TestCaseBatch, optionally including OpenAPI semantics."""
    try:
        batch = load_test_case_batch(cases)
    except TestCaseBatchLoadError as error:
        if json_output:
            typer.echo(json.dumps({"valid": False, "path": str(cases), "error": str(error)}))
        else:
            typer.echo(f"Invalid TestCaseBatch: {cases}\n{error}", err=True)
        raise typer.Exit(code=1) from error

    if spec is not None:
        try:
            openapi_spec = load_openapi(spec)
        except SpecLoadError as error:
            if json_output:
                typer.echo(
                    json.dumps(
                        {
                            "valid": False,
                            "stage": "openapi",
                            "path": str(spec),
                            "error": str(error),
                        }
                    )
                )
            else:
                typer.echo(f"Invalid OpenAPI document: {spec}\n{error}", err=True)
            raise typer.Exit(code=1) from error

        semantic_issues = validate_test_case_batch_semantics(batch, openapi_spec)
        if semantic_issues:
            _report_case_semantic_issues(cases, spec, semantic_issues, json_output)

    result = _case_batch_summary(batch, cases)
    if spec is not None:
        result.update(
            {
                "spec": str(spec),
                "spec_id": openapi_spec.spec_id,
                "semantic": True,
            }
        )
    if json_output:
        typer.echo(json.dumps(result))
    else:
        typer.echo(
            "Valid TestCaseBatch: "
            f"{_count_label(result['cases'], 'case')}, "
            f"{_count_label(result['steps'], 'step')}, "
            f"{_count_label(result['relations'], 'relation')}"
            + (f"; OpenAPI semantics match {result['spec_id']}" if spec is not None else "")
        )


@cases_app.command("run")
def run_cases(
    spec: Annotated[
        Path,
        typer.Option("--spec", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    cases: Annotated[
        Path,
        typer.Option("--cases", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    base_url: Annotated[
        str,
        typer.Option("--base-url", help="Explicit HTTP(S) base URL of the test target."),
    ],
    out: Annotated[
        Path | None,
        typer.Option("--out", file_okay=True, dir_okay=False),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the complete RunResult JSON."),
    ] = False,
    timeout_ms: Annotated[
        int,
        typer.Option("--timeout-ms", help="Per-request timeout in milliseconds."),
    ] = 5000,
    allow_mutations: Annotated[
        bool,
        typer.Option(
            "--allow-mutations",
            help="Confirm that POST/PUT/PATCH/DELETE target an isolated test environment.",
        ),
    ] = False,
) -> None:
    """Validate and execute one TestCaseBatch against an explicit target URL."""
    batch, openapi_spec = _load_validated_case_inputs(cases, spec, json_output)
    try:
        result = execute_test_case_batch(
            batch,
            openapi_spec,
            base_url,
            batch_name=cases.stem,
            timeout_ms=timeout_ms,
            allow_mutations=allow_mutations,
        )
    except ValueError as error:
        _report_case_run_input_error("runner", base_url, error, json_output)

    serialized = result.model_dump_json(indent=2)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"{serialized}\n", encoding="utf-8")

    if json_output:
        typer.echo(serialized)
    else:
        destination = f"; wrote {out}" if out is not None else ""
        typer.echo(
            f"Run {result.run_id}: {result.outcome.value} "
            f"({_count_label(len(result.cases), 'case')}, {result.duration_ms} ms){destination}"
        )

    if result.outcome is not ExecutionOutcome.PASSED:
        raise typer.Exit(code=1)


def _load_validated_case_inputs(
    cases_path: Path,
    spec_path: Path,
    json_output: bool,
) -> tuple[TestCaseBatch, OpenAPISpec]:
    try:
        batch = load_test_case_batch(cases_path)
    except TestCaseBatchLoadError as error:
        _report_case_run_input_error("cases", str(cases_path), error, json_output)

    try:
        openapi_spec = load_openapi(spec_path)
    except SpecLoadError as error:
        _report_case_run_input_error("openapi", str(spec_path), error, json_output)

    semantic_issues = validate_test_case_batch_semantics(batch, openapi_spec)
    if semantic_issues:
        _report_case_semantic_issues(cases_path, spec_path, semantic_issues, json_output)
    return batch, openapi_spec


def _report_case_semantic_issues(
    cases_path: Path,
    spec_path: Path,
    issues: list[SemanticIssue],
    json_output: bool,
) -> NoReturn:
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "valid": False,
                    "stage": "semantic",
                    "cases": str(cases_path),
                    "spec": str(spec_path),
                    "issues": [issue.model_dump() for issue in issues],
                }
            )
        )
    else:
        typer.echo(f"Invalid TestCaseBatch semantics: {cases_path}", err=True)
        for issue in issues:
            typer.echo(f"- [{issue.code}] {issue.path}: {issue.message}", err=True)
    raise typer.Exit(code=1)


def _report_case_run_input_error(
    stage: str,
    path: str,
    error: Exception,
    json_output: bool,
) -> NoReturn:
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "valid": False,
                    "stage": stage,
                    "path": path,
                    "error": str(error),
                }
            )
        )
    else:
        typer.echo(f"Cannot run TestCaseBatch ({stage}): {path}\n{error}", err=True)
    raise typer.Exit(code=1)


def _case_batch_summary(batch: TestCaseBatch, path: Path) -> dict[str, object]:
    return {
        "valid": True,
        "path": str(path),
        "cases": len(batch.cases),
        "steps": sum(len(case.setup) + len(case.steps) + len(case.cleanup) for case in batch.cases),
        "relations": sum(len(case.relations) for case in batch.cases),
    }


def _count_label(value: object, singular: str) -> str:
    count = int(value)
    suffix = "" if count == 1 else "s"
    return f"{count} {singular}{suffix}"


@app.command("run")
def run_plan(
    spec: Annotated[
        Path,
        typer.Option("--spec", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    plan: Annotated[
        Path,
        typer.Option("--plan", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    base_url: Annotated[
        str,
        typer.Option("--base-url", help="Explicit HTTP(S) base URL of the test target."),
    ],
    out: Annotated[
        Path | None,
        typer.Option("--out", file_okay=True, dir_okay=False),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the complete RunResult JSON."),
    ] = False,
    allow_mutations: Annotated[
        bool,
        typer.Option(
            "--allow-mutations",
            help="Confirm that POST/PUT/PATCH/DELETE target an isolated test environment.",
        ),
    ] = False,
) -> None:
    """Validate and execute one TestPlan against an explicit target URL."""
    test_plan, openapi_spec = _load_validated_run_inputs(plan, spec, json_output)
    try:
        result = execute_test_plan(
            test_plan,
            openapi_spec,
            base_url,
            allow_mutations=allow_mutations,
        )
    except ValueError as error:
        _report_run_input_error("runner", base_url, error, json_output)

    serialized = result.model_dump_json(indent=2)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"{serialized}\n", encoding="utf-8")

    if json_output:
        typer.echo(serialized)
    else:
        destination = f"; wrote {out}" if out is not None else ""
        typer.echo(
            f"Run {result.run_id}: {result.outcome.value} "
            f"({_count_label(len(result.cases), 'case')}, {result.duration_ms} ms){destination}"
        )

    if result.outcome is not ExecutionOutcome.PASSED:
        raise typer.Exit(code=1)


def _load_validated_run_inputs(
    plan_path: Path,
    spec_path: Path,
    json_output: bool,
) -> tuple[TestPlan, OpenAPISpec]:
    try:
        test_plan = load_test_plan(plan_path)
    except PlanLoadError as error:
        _report_run_input_error("plan", str(plan_path), error, json_output)

    try:
        openapi_spec = load_openapi(spec_path)
    except SpecLoadError as error:
        _report_run_input_error("openapi", str(spec_path), error, json_output)

    semantic_issues = validate_plan_semantics(test_plan, openapi_spec)
    if semantic_issues:
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "valid": False,
                        "stage": "semantic",
                        "plan": str(plan_path),
                        "spec": str(spec_path),
                        "issues": [issue.model_dump() for issue in semantic_issues],
                    }
                )
            )
        else:
            typer.echo(f"Invalid TestPlan semantics: {plan_path}", err=True)
            for issue in semantic_issues:
                typer.echo(
                    f"- [{issue.code}] {issue.path}: {issue.message}",
                    err=True,
                )
        raise typer.Exit(code=1)
    return test_plan, openapi_spec


def _report_run_input_error(
    stage: str,
    path: str,
    error: Exception,
    json_output: bool,
) -> NoReturn:
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "valid": False,
                    "stage": stage,
                    "path": path,
                    "error": str(error),
                }
            )
        )
    else:
        typer.echo(f"Cannot run TestPlan ({stage}): {path}\n{error}", err=True)
    raise typer.Exit(code=1)


@plan_app.command("validate")
def validate_plan(
    plan: Annotated[
        Path,
        typer.Option("--plan", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Also validate the plan against a supported OpenAPI 3.0/3.1 document.",
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    """Validate a TestPlan, optionally including OpenAPI semantics."""
    try:
        test_plan = load_test_plan(plan)
    except PlanLoadError as error:
        if json_output:
            typer.echo(json.dumps({"valid": False, "path": str(plan), "error": str(error)}))
        else:
            typer.echo(f"Invalid TestPlan: {plan}\n{error}", err=True)
        raise typer.Exit(code=1) from error

    if spec is not None:
        try:
            openapi_spec = load_openapi(spec)
        except SpecLoadError as error:
            if json_output:
                typer.echo(
                    json.dumps(
                        {
                            "valid": False,
                            "stage": "openapi",
                            "path": str(spec),
                            "error": str(error),
                        }
                    )
                )
            else:
                typer.echo(f"Invalid OpenAPI document: {spec}\n{error}", err=True)
            raise typer.Exit(code=1) from error

        semantic_issues = validate_plan_semantics(test_plan, openapi_spec)
        if semantic_issues:
            if json_output:
                typer.echo(
                    json.dumps(
                        {
                            "valid": False,
                            "stage": "semantic",
                            "plan": str(plan),
                            "spec": str(spec),
                            "issues": [issue.model_dump() for issue in semantic_issues],
                        }
                    )
                )
            else:
                typer.echo(f"Invalid TestPlan semantics: {plan}", err=True)
                for issue in semantic_issues:
                    typer.echo(
                        f"- [{issue.code}] {issue.path}: {issue.message}",
                        err=True,
                    )
            raise typer.Exit(code=1)

    result = {
        "valid": True,
        "path": str(plan),
        "scenarios": len(test_plan.scenarios),
        "steps": sum(
            len(scenario.setup) + len(scenario.steps) + len(scenario.cleanup)
            for scenario in test_plan.scenarios
        ),
        "relations": sum(len(scenario.relations) for scenario in test_plan.scenarios),
    }
    if spec is not None:
        result["spec"] = str(spec)
        result["spec_id"] = openapi_spec.spec_id
        result["semantic"] = True
    if json_output:
        typer.echo(json.dumps(result))
    else:
        typer.echo(
            "Valid TestPlan: "
            f"{result['scenarios']} scenarios, {result['steps']} steps, "
            f"{result['relations']} relations"
            + (f"; OpenAPI semantics match {result['spec_id']}" if spec is not None else "")
        )


@plan_app.command("schema")
def export_plan_schema(
    out: Annotated[
        Path,
        typer.Option("--out", file_okay=True, dir_okay=False),
    ] = Path("schemas/test-plan.schema.json"),
) -> None:
    """Export the JSON Schema generated from the Pydantic contract."""
    out.parent.mkdir(parents=True, exist_ok=True)
    schema = TestPlan.model_json_schema()
    out.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"Wrote TestPlan schema: {out}")
