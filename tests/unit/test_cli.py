import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openapi_ai_test_evaluator.cli.app import app
from openapi_ai_test_evaluator.domain.execution import (
    ExecutionOutcome,
    FaultObservation,
    FaultTriggerStatus,
    RunResult,
    ScenarioResult,
    StepPhase,
    StepResult,
)

ROOT = Path(__file__).parents[2]
runner = CliRunner()


def run_result(outcome: ExecutionOutcome = ExecutionOutcome.PASSED) -> RunResult:
    now = datetime(2026, 8, 23, 10, tzinfo=UTC)
    return RunResult(
        schema_version="1.0",
        kind="RunResult",
        run_id="run-cli",
        plan_name="minimal-get",
        spec_id="demo-items-v1",
        started_at=now,
        finished_at=now,
        duration_ms=0,
        outcome=outcome,
        fault=FaultObservation(
            configured_fault_id=None,
            trigger_status=FaultTriggerStatus.NOT_CONFIGURED,
            trigger_count=0,
        ),
        scenarios=[
            ScenarioResult(
                scenario_id="list-items",
                outcome=outcome,
                steps=[
                    StepResult(
                        phase=StepPhase.MAIN,
                        step_id="list",
                        operation_id="listItems",
                        outcome_policy="required",
                        outcome=outcome,
                        duration_ms=0,
                        retry_count=0,
                        request=None,
                        response=None,
                        extractions=[],
                        assertions=[],
                        errors=[],
                    )
                ],
                relations=[],
                errors=[],
            )
        ],
        errors=[],
    )


def test_plan_validate_reports_counts() -> None:
    plan_path = ROOT / "examples" / "plans" / "minimal-get.yaml"

    result = runner.invoke(app, ["plan", "validate", "--plan", str(plan_path), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "valid": True,
        "path": str(plan_path),
        "scenarios": 1,
        "steps": 1,
        "relations": 0,
    }


def test_plan_validate_checks_openapi_semantics() -> None:
    plan_path = ROOT / "examples" / "plans" / "all-methods.yaml"
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"

    result = runner.invoke(
        app,
        [
            "plan",
            "validate",
            "--plan",
            str(plan_path),
            "--spec",
            str(spec_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["valid"] is True
    assert output["semantic"] is True
    assert output["spec_id"] == "demo-items-v1"


def test_plan_validate_returns_semantic_issues(tmp_path: Path) -> None:
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"
    plan_path = tmp_path / "unknown-operation.yaml"
    source = (ROOT / "examples" / "plans" / "minimal-get.yaml").read_text(encoding="utf-8")
    plan_path.write_text(source.replace("listItems", "missingOperation"), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "plan",
            "validate",
            "--plan",
            str(plan_path),
            "--spec",
            str(spec_path),
            "--json",
        ],
    )

    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["stage"] == "semantic"
    assert output["issues"][0]["code"] == "unknown_operation"


def test_plan_validate_returns_nonzero_for_invalid_plan() -> None:
    plan_path = (
        ROOT / "tests" / "fixtures" / "plans" / "invalid" / "invalid-without-violations.yaml"
    )

    result = runner.invoke(app, ["plan", "validate", "--plan", str(plan_path)])

    assert result.exit_code == 1
    assert "Invalid TestPlan" in result.output


def test_plan_validate_reports_structural_failure_as_json() -> None:
    plan_path = (
        ROOT / "tests" / "fixtures" / "plans" / "invalid" / "invalid-without-violations.yaml"
    )

    result = runner.invoke(
        app,
        ["plan", "validate", "--plan", str(plan_path), "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["valid"] is False


def test_plan_validate_reports_invalid_openapi_as_json(tmp_path: Path) -> None:
    plan_path = ROOT / "examples" / "plans" / "minimal-get.yaml"
    spec_path = tmp_path / "invalid-openapi.yaml"
    spec_path.write_text("openapi: 3.0.3\ninfo: {}\npaths: {}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "plan",
            "validate",
            "--plan",
            str(plan_path),
            "--spec",
            str(spec_path),
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["stage"] == "openapi"


def test_plan_validate_reports_semantic_failure_as_text(tmp_path: Path) -> None:
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"
    plan_path = tmp_path / "unknown-operation.yaml"
    source = (ROOT / "examples" / "plans" / "minimal-get.yaml").read_text(encoding="utf-8")
    plan_path.write_text(source.replace("listItems", "missingOperation"), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "plan",
            "validate",
            "--plan",
            str(plan_path),
            "--spec",
            str(spec_path),
        ],
    )

    assert result.exit_code == 1
    assert "[unknown_operation]" in result.output


def test_plan_schema_writes_generated_schema(tmp_path: Path) -> None:
    output_path = tmp_path / "test-plan.schema.json"

    result = runner.invoke(app, ["plan", "schema", "--out", str(output_path)])

    assert result.exit_code == 0
    schema = json.loads(output_path.read_text(encoding="utf-8"))
    assert schema["title"] == "TestPlan"


def test_run_command_emits_and_writes_complete_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_path = ROOT / "examples" / "plans" / "minimal-get.yaml"
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"
    output_path = tmp_path / "run-result.json"

    def fake_execute(*args: object, **kwargs: object) -> RunResult:
        assert args[2] == "https://example.test/api"
        return run_result()

    monkeypatch.setattr(
        "openapi_ai_test_evaluator.cli.app.execute_test_plan",
        fake_execute,
    )

    result = runner.invoke(
        app,
        [
            "run",
            "--spec",
            str(spec_path),
            "--plan",
            str(plan_path),
            "--base-url",
            "https://example.test/api",
            "--out",
            str(output_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["kind"] == "RunResult"
    assert json.loads(output_path.read_text(encoding="utf-8"))["run_id"] == "run-cli"


def test_run_command_returns_nonzero_for_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_path = ROOT / "examples" / "plans" / "minimal-get.yaml"
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"
    monkeypatch.setattr(
        "openapi_ai_test_evaluator.cli.app.execute_test_plan",
        lambda *args, **kwargs: run_result(ExecutionOutcome.FAILED),
    )

    result = runner.invoke(
        app,
        [
            "run",
            "--spec",
            str(spec_path),
            "--plan",
            str(plan_path),
            "--base-url",
            "https://example.test/api",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["outcome"] == "failed"


def test_run_command_rejects_invalid_base_url_before_network() -> None:
    plan_path = ROOT / "examples" / "plans" / "minimal-get.yaml"
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"

    result = runner.invoke(
        app,
        [
            "run",
            "--spec",
            str(spec_path),
            "--plan",
            str(plan_path),
            "--base-url",
            "ftp://example.test/api",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["stage"] == "runner"
