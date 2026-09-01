import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openapi_ai_test_evaluator.cli.app import app
from openapi_ai_test_evaluator.domain.evaluation import EvaluationResult
from openapi_ai_test_evaluator.reporting import (
    ComparisonInputError,
    compare_evaluations,
    render_comparison_markdown,
)

runner = CliRunner()


def evaluation(
    suite_id: str,
    repetition: int,
    *,
    generator_kind: str,
    generator_name: str,
    received: int,
    admitted: int,
    covered_operations: int,
    detected: bool,
    generation_duration_ms: int,
    estimated_cost_usd: float | None,
) -> EvaluationResult:
    fault_outcome = "detected" if detected else "not_detected"
    fault_requests = 10
    generator = {
        "kind": generator_kind,
        "name": generator_name,
        "version": "4.25.2" if generator_kind == "schema_tool" else None,
        "model": "deepseek-v4-flash" if generator_kind == "llm" else None,
        "source_record_id": (
            f"generation-{suite_id}-{repetition}" if generator_kind == "llm" else None
        ),
        "generation_request_count": 1 if generator_kind == "llm" else 0,
        "generation_duration_ms": generation_duration_ms,
        "token_usage": {
            "input_tokens": 100 if generator_kind == "llm" else None,
            "output_tokens": 50 if generator_kind == "llm" else None,
            "total_tokens": 150 if generator_kind == "llm" else None,
            "cached_input_tokens": 20 if generator_kind == "llm" else None,
            "reasoning_tokens": 10 if generator_kind == "llm" else None,
        },
        "estimated_cost_usd": estimated_cost_usd,
    }
    fault = {
        "fault_id": "missing-name",
        "run_id": f"fault-{suite_id}-{repetition}",
        "outcome": fault_outcome,
        "trigger_count": 1,
        "request_count": fault_requests,
        "triggered_case_ids": ["case-1"],
        "eligible_triggered_case_ids": ["case-1"],
        "detected_case_ids": ["case-1"] if detected else [],
        "errored_case_ids": [],
        "first_detection_request": 2 if detected else None,
    }
    return EvaluationResult.model_validate(
        {
            "schema_version": "1.0",
            "kind": "EvaluationResult",
            "evaluation_id": f"evaluation-{suite_id}-{repetition}",
            "suite_id": suite_id,
            "repetition": repetition,
            "spec_id": "demo-items-spec",
            "generator": generator,
            "admission": {
                "received_case_count": received,
                "admitted_case_count": admitted,
                "rejected_case_count": received - admitted,
                "admission_rate": admitted / received,
            },
            "execution": {
                "admitted_case_count": admitted,
                "clean_passed_case_count": admitted,
                "clean_failed_case_count": 0,
                "clean_error_case_count": 0,
                "clean_skipped_case_count": 0,
                "executable_case_rate": 1,
                "clean_false_positive_rate": 0,
                "eligible_operation_count": 4,
                "covered_operation_count": covered_operations,
                "operation_coverage_rate": covered_operations / 4,
                "clean_request_count": admitted,
                "fault_request_count": fault_requests,
                "total_request_count": admitted + fault_requests,
                "execution_duration_ms": 50 + repetition,
            },
            "fault_summary": {
                "configured_fault_count": 1,
                "triggered_fault_count": 1,
                "evaluable_fault_count": 1,
                "detected_fault_count": int(detected),
                "not_detected_fault_count": int(not detected),
                "not_triggered_fault_count": 0,
                "no_eligible_case_fault_count": 0,
                "inconclusive_fault_count": 0,
                "fault_detection_rate": int(detected),
                "faults_detected_per_100_requests": int(detected) * 100 / fault_requests,
            },
            "clean_run_id": f"clean-{suite_id}-{repetition}",
            "faults": [fault],
        }
    )


def paired_evaluations() -> list[EvaluationResult]:
    return [
        evaluation(
            "deepseek",
            1,
            generator_kind="llm",
            generator_name="deepseek",
            received=4,
            admitted=2,
            covered_operations=2,
            detected=True,
            generation_duration_ms=100,
            estimated_cost_usd=0.001,
        ),
        evaluation(
            "deepseek",
            2,
            generator_kind="llm",
            generator_name="deepseek",
            received=4,
            admitted=3,
            covered_operations=3,
            detected=False,
            generation_duration_ms=200,
            estimated_cost_usd=0.003,
        ),
        evaluation(
            "schemathesis",
            1,
            generator_kind="schema_tool",
            generator_name="schemathesis",
            received=10,
            admitted=10,
            covered_operations=4,
            detected=True,
            generation_duration_ms=40,
            estimated_cost_usd=0.0,
        ),
        evaluation(
            "schemathesis",
            2,
            generator_kind="schema_tool",
            generator_name="schemathesis",
            received=10,
            admitted=10,
            covered_operations=4,
            detected=True,
            generation_duration_ms=60,
            estimated_cost_usd=0.0,
        ),
    ]


def with_shared_enhancement(item: EvaluationResult, count: int = 2) -> EvaluationResult:
    raw = item.model_dump(mode="json")
    raw["composition"] = {
        "composition_id": f"composition-{item.suite_id}-r{item.repetition}",
        "enhancement_case_count": count,
        "enhancement_pack_ids": ["shared-relations-v1"],
        "composed_batch_sha256": "a" * 64,
    }
    raw["execution"]["admitted_case_count"] += count
    raw["execution"]["clean_passed_case_count"] += count
    raw["execution"]["clean_request_count"] += count
    raw["execution"]["total_request_count"] += count
    return EvaluationResult.model_validate(raw)


def test_aggregates_raw_normalized_cost_and_fault_stability_metrics() -> None:
    comparison = compare_evaluations(paired_evaluations(), comparison_id="deepseek-vs-schemathesis")

    assert comparison.repetitions == [1, 2]
    assert comparison.fault_ids == ["missing-name"]
    deepseek, schemathesis = comparison.suites
    assert deepseek.suite_id == "deepseek"
    assert deepseek.admitted_case_count.values == [2.0, 3.0]
    assert deepseek.enhancement_case_count.values == [0.0, 0.0]
    assert deepseek.executed_case_count.values == [2.0, 3.0]
    assert deepseek.admission_rate.mean == pytest.approx(0.625)
    assert deepseek.admission_rate.stddev == pytest.approx(0.125)
    assert deepseek.fault_detection_rate.mean == pytest.approx(0.5)
    assert deepseek.estimated_cost_usd.mean == pytest.approx(0.002)
    assert deepseek.total_tokens.mean == pytest.approx(150)
    assert deepseek.cached_input_tokens.mean == pytest.approx(20)
    assert deepseek.reasoning_tokens.mean == pytest.approx(10)
    assert deepseek.faults[0].detection_rate == pytest.approx(0.5)
    assert deepseek.faults[0].first_detection_request.values == [2.0]
    assert deepseek.faults[0].first_detection_request.missing_count == 1
    assert schemathesis.suite_id == "schemathesis"
    assert schemathesis.estimated_cost_usd.values == [0.0, 0.0]
    assert schemathesis.estimated_cost_usd.mean == 0.0


def test_reports_native_and_shared_case_counts_for_mixed_four_arm_comparisons() -> None:
    evaluations = [
        with_shared_enhancement(item) if item.suite_id == "deepseek" else item
        for item in paired_evaluations()
    ]

    comparison = compare_evaluations(evaluations, comparison_id="native-and-enhanced")

    assert comparison.mode.value == "mixed_suite"
    deepseek, schemathesis = comparison.suites
    assert deepseek.admitted_case_count.values == [2.0, 3.0]
    assert deepseek.enhancement_case_count.values == [2.0, 2.0]
    assert deepseek.executed_case_count.values == [4.0, 5.0]
    assert schemathesis.enhancement_case_count.values == [0.0, 0.0]


def test_renders_a_human_readable_report_with_source_traceability() -> None:
    comparison = compare_evaluations(paired_evaluations(), comparison_id="deepseek-vs-schemathesis")

    report = render_comparison_markdown(comparison)

    assert "# API Test Generation Comparison: deepseek-vs-schemathesis" in report
    assert "Cases executed / shared" in report
    assert "`deepseek`" in report
    assert "62.5% ± 12.5%" in report
    assert "D/M/T/E/I=1/1/0/0/0" in report
    assert "evaluation-schemathesis-2" in report
    assert "n/a" in report


def test_rejects_unpaired_repetitions() -> None:
    evaluations = paired_evaluations()
    evaluations.pop()

    with pytest.raises(ComparisonInputError, match="same paired repetitions"):
        compare_evaluations(evaluations, comparison_id="unpaired")


def test_rejects_generator_identity_changes_within_one_suite() -> None:
    evaluations = paired_evaluations()
    evaluations[1] = evaluations[1].model_copy(
        update={"generator": evaluations[1].generator.model_copy(update={"model": "another-model"})}
    )

    with pytest.raises(ComparisonInputError, match="changes generator identity"):
        compare_evaluations(evaluations, comparison_id="changed-generator")


def test_report_compare_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    arguments = ["report", "compare"]
    for item in paired_evaluations():
        path = tmp_path / f"{item.evaluation_id}.json"
        path.write_text(item.model_dump_json(indent=2) + "\n", encoding="utf-8")
        arguments.extend(["--evaluation", str(path)])
    json_output = tmp_path / "reports" / "comparison.json"
    markdown_output = tmp_path / "reports" / "comparison.md"
    arguments.extend(
        [
            "--comparison-id",
            "deepseek-vs-schemathesis",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
            "--json",
        ]
    )

    result = runner.invoke(app, arguments)

    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert summary["suites"] == 2
    assert summary["repetitions"] == 2
    saved = json.loads(json_output.read_text(encoding="utf-8"))
    assert saved["kind"] == "ComparisonResult"
    assert len(saved["suites"]) == 2
    assert "Per-Fault Stability" in markdown_output.read_text(encoding="utf-8")


def test_report_compare_cli_rejects_invalid_evaluation_artifact(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    other = tmp_path / "other.json"
    invalid.write_text("{}\n", encoding="utf-8")
    other.write_text("{}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "report",
            "compare",
            "--evaluation",
            str(invalid),
            "--evaluation",
            str(other),
            "--comparison-id",
            "invalid",
            "--json-output",
            str(tmp_path / "comparison.json"),
            "--markdown-output",
            str(tmp_path / "comparison.md"),
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["stage"] == "evaluation-artifacts"
    assert not (tmp_path / "comparison.json").exists()
