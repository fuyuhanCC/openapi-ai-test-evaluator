import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from openapi_ai_test_evaluator.cli.app import app
from openapi_ai_test_evaluator.domain import SuiteCompositionRecord
from openapi_ai_test_evaluator.domain.execution import (
    ExecutionOutcome,
    FaultObservation,
    FaultTriggerStatus,
    RunResult,
    StepPhase,
    StepResult,
)
from openapi_ai_test_evaluator.domain.execution import TestCaseResult as CaseResult
from openapi_ai_test_evaluator.generation import DeepSeekProvider, case_batch_sha256
from openapi_ai_test_evaluator.validation import load_test_case_batch

ROOT = Path(__file__).parents[2]
runner = CliRunner()


def deepseek_response(content: str, *, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "id": "completion-cli",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_cache_hit_tokens": 0,
            },
        },
    )


def generated_list_case_batch() -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "cases": [
                {
                    "id": "generated-list-items",
                    "steps": [
                        {
                            "id": "list-items",
                            "operation_id": "listItems",
                            "assertions": [
                                {"operator": "status_is", "expected": 200},
                                {"operator": "schema_matches"},
                            ],
                        }
                    ],
                }
            ],
        }
    )


def test_cases_generate_writes_cases_and_generation_record_with_mock_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"
    cases_output = tmp_path / "cases" / "deepseek.json"
    record_output = tmp_path / "generations" / "deepseek.json"
    raw_output = tmp_path / "raw" / "deepseek.txt"
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return deepseek_response(generated_list_case_batch())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekProvider("test-key", client=client)
        monkeypatch.setattr(
            "openapi_ai_test_evaluator.cli.app._deepseek_provider_from_env",
            lambda: provider,
        )
        result = runner.invoke(
            app,
            [
                "cases",
                "generate",
                "--spec",
                str(spec_path),
                "--provider",
                "deepseek",
                "--model",
                "deepseek-v4-flash",
                "--generation-id",
                "generation-cli",
                "--cases-output",
                str(cases_output),
                "--record-output",
                str(record_output),
                "--raw-output",
                str(raw_output),
                "--json",
            ],
        )

    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert summary["status"] == "succeeded"
    assert summary["cases"] == 1
    assert summary["received_cases"] == 1
    assert summary["admitted_cases"] == 1
    assert summary["rejected_cases"] == 0
    assert summary["cases_output"] == str(cases_output)
    assert summary["raw_output"] == str(raw_output)
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["response_format"] == {"type": "json_object"}

    saved_cases = json.loads(cases_output.read_text(encoding="utf-8"))
    saved_record = json.loads(record_output.read_text(encoding="utf-8"))
    assert saved_cases["cases"][0]["id"] == "generated-list-items"
    assert saved_record["kind"] == "GenerationRecord"
    assert saved_record["generation_id"] == "generation-cli"
    assert saved_record["token_usage"]["total_tokens"] == 120
    assert saved_record["case_admission"]["admitted_case_count"] == 1
    assert raw_output.read_text(encoding="utf-8") == f"{generated_list_case_batch()}\n"


def test_cases_generate_writes_failure_record_but_not_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"
    cases_output = tmp_path / "cases.json"
    record_output = tmp_path / "record.json"
    raw_output = tmp_path / "raw.txt"

    with httpx.Client(
        transport=httpx.MockTransport(lambda request: deepseek_response("{}"))
    ) as client:
        provider = DeepSeekProvider("test-key", client=client)
        monkeypatch.setattr(
            "openapi_ai_test_evaluator.cli.app._deepseek_provider_from_env",
            lambda: provider,
        )
        result = runner.invoke(
            app,
            [
                "cases",
                "generate",
                "--spec",
                str(spec_path),
                "--generation-id",
                "generation-invalid",
                "--cases-output",
                str(cases_output),
                "--record-output",
                str(record_output),
                "--raw-output",
                str(raw_output),
                "--json",
            ],
        )

    assert result.exit_code == 1
    summary = json.loads(result.stdout)
    assert summary["status"] == "invalid_output"
    assert summary["raw_output"] == str(raw_output)
    assert not cases_output.exists()
    assert raw_output.read_text(encoding="utf-8") == "{}\n"
    saved_record = json.loads(record_output.read_text(encoding="utf-8"))
    assert saved_record["status"] == "invalid_output"
    assert saved_record["error"]["code"] == "invalid-test-case-batch"


def test_cases_generate_reports_missing_api_key_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    result = runner.invoke(
        app,
        [
            "cases",
            "generate",
            "--spec",
            str(spec_path),
            "--cases-output",
            str(tmp_path / "cases.json"),
            "--record-output",
            str(tmp_path / "record.json"),
            "--raw-output",
            str(tmp_path / "raw.txt"),
            "--json",
        ],
    )

    assert result.exit_code == 1
    summary = json.loads(result.stdout)
    assert summary["status"] == "not_started"
    assert summary["stage"] == "provider-config"
    assert "DEEPSEEK_API_KEY" in summary["error"]
    assert not (tmp_path / "raw.txt").exists()


def test_cases_generate_refuses_to_overwrite_before_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"
    cases_output = tmp_path / "cases.json"
    record_output = tmp_path / "record.json"
    raw_output = tmp_path / "raw.txt"
    cases_output.write_text("keep-me", encoding="utf-8")
    provider_created = False

    def provider_factory() -> DeepSeekProvider:
        nonlocal provider_created
        provider_created = True
        raise AssertionError("provider must not be created")

    monkeypatch.setattr(
        "openapi_ai_test_evaluator.cli.app._deepseek_provider_from_env",
        provider_factory,
    )
    result = runner.invoke(
        app,
        [
            "cases",
            "generate",
            "--spec",
            str(spec_path),
            "--cases-output",
            str(cases_output),
            "--record-output",
            str(record_output),
            "--raw-output",
            str(raw_output),
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["stage"] == "artifacts"
    assert cases_output.read_text(encoding="utf-8") == "keep-me"
    assert provider_created is False


def test_cases_generate_baseline_writes_cases_and_adaptation_record(tmp_path: Path) -> None:
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"
    cases_output = tmp_path / "cases" / "schemathesis.json"
    record_output = tmp_path / "adaptations" / "schemathesis.json"

    result = runner.invoke(
        app,
        [
            "cases",
            "generate-baseline",
            "--spec",
            str(spec_path),
            "--cases-output",
            str(cases_output),
            "--record-output",
            str(record_output),
            "--no-examples",
            "--no-coverage",
            "--fuzz-pos",
            "1",
            "--fuzz-neg",
            "0",
            "--seed",
            "7",
            "--json",
        ],
    )

    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert summary["status"] == "succeeded"
    assert summary["received_cases"] == 6
    assert summary["adapted_cases"] == 6
    assert summary["rejected_cases"] == 0
    assert summary["cases_output"] == str(cases_output)
    saved_cases = json.loads(cases_output.read_text(encoding="utf-8"))
    saved_record = json.loads(record_output.read_text(encoding="utf-8"))
    assert saved_cases["cases"][0]["id"] == "schemathesis-0001"
    assert saved_record["kind"] == "AdaptationRecord"
    assert saved_record["seed"] == 7


def test_cases_generate_baseline_rejects_empty_budget_before_writing(tmp_path: Path) -> None:
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"
    cases_output = tmp_path / "cases.json"
    record_output = tmp_path / "record.json"

    result = runner.invoke(
        app,
        [
            "cases",
            "generate-baseline",
            "--spec",
            str(spec_path),
            "--cases-output",
            str(cases_output),
            "--record-output",
            str(record_output),
            "--no-examples",
            "--no-coverage",
            "--fuzz-pos",
            "0",
            "--fuzz-neg",
            "0",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["stage"] == "config"
    assert not cases_output.exists()
    assert not record_output.exists()


def test_cases_generate_baseline_refuses_to_overwrite_existing_artifacts(
    tmp_path: Path,
) -> None:
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"
    cases_output = tmp_path / "cases.json"
    record_output = tmp_path / "record.json"
    record_output.write_text("keep-me", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "cases",
            "generate-baseline",
            "--spec",
            str(spec_path),
            "--cases-output",
            str(cases_output),
            "--record-output",
            str(record_output),
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["stage"] == "artifacts"
    assert record_output.read_text(encoding="utf-8") == "keep-me"
    assert not cases_output.exists()


def run_result(outcome: ExecutionOutcome = ExecutionOutcome.PASSED) -> RunResult:
    now = datetime(2026, 8, 23, 10, tzinfo=UTC)
    return RunResult(
        schema_version="2.0",
        kind="RunResult",
        run_id="run-cli",
        batch_name="minimal-get",
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
        cases=[
            CaseResult(
                case_id="list-items",
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


def test_cases_validate_reports_counts() -> None:
    cases_path = ROOT / "examples" / "cases" / "minimal-get.yaml"

    result = runner.invoke(app, ["cases", "validate", "--cases", str(cases_path), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "valid": True,
        "path": str(cases_path),
        "cases": 1,
        "steps": 1,
        "relations": 0,
    }


def test_cases_compose_writes_merged_batch_and_provenance_record(tmp_path: Path) -> None:
    base_path = ROOT / "examples" / "cases" / "minimal-get.yaml"
    enhancement_path = (
        ROOT / "benchmarks" / "demo_items" / "enhancements" / "shared-relations.yaml"
    )
    cases_output = tmp_path / "cases" / "augmented.json"
    record_output = tmp_path / "compositions" / "augmented.json"

    result = runner.invoke(
        app,
        [
            "cases",
            "compose",
            "--base-cases",
            str(base_path),
            "--enhancement-cases",
            str(enhancement_path),
            "--pack-id",
            "shared-relations",
            "--composition-id",
            "demo-augmented-r1",
            "--cases-output",
            str(cases_output),
            "--record-output",
            str(record_output),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "status": "succeeded",
        "composition_id": "demo-augmented-r1",
        "base_cases": 1,
        "enhancement_cases": 7,
        "composed_cases": 8,
        "cases_output": str(cases_output),
        "record_output": str(record_output),
    }
    composed_batch = load_test_case_batch(cases_output)
    record = SuiteCompositionRecord.model_validate_json(record_output.read_text())
    assert len(composed_batch.cases) == 8
    assert record.composed_batch.sha256 == case_batch_sha256(composed_batch)


def test_cases_compose_requires_one_pack_id_per_enhancement(tmp_path: Path) -> None:
    base_path = ROOT / "examples" / "cases" / "minimal-get.yaml"
    enhancement_path = (
        ROOT / "benchmarks" / "demo_items" / "enhancements" / "shared-relations.yaml"
    )

    result = runner.invoke(
        app,
        [
            "cases",
            "compose",
            "--base-cases",
            str(base_path),
            "--enhancement-cases",
            str(enhancement_path),
            "--enhancement-cases",
            str(enhancement_path),
            "--pack-id",
            "shared-relations",
            "--composition-id",
            "demo-augmented-r1",
            "--cases-output",
            str(tmp_path / "cases.json"),
            "--record-output",
            str(tmp_path / "record.json"),
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["stage"] == "config"


def test_cases_compose_refuses_to_overwrite_existing_outputs(tmp_path: Path) -> None:
    base_path = ROOT / "examples" / "cases" / "minimal-get.yaml"
    enhancement_path = (
        ROOT / "benchmarks" / "demo_items" / "enhancements" / "shared-relations.yaml"
    )
    cases_output = tmp_path / "cases.json"
    record_output = tmp_path / "record.json"
    cases_output.write_text("keep-me", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "cases",
            "compose",
            "--base-cases",
            str(base_path),
            "--enhancement-cases",
            str(enhancement_path),
            "--pack-id",
            "shared-relations",
            "--composition-id",
            "demo-augmented-r1",
            "--cases-output",
            str(cases_output),
            "--record-output",
            str(record_output),
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["stage"] == "artifacts"
    assert cases_output.read_text(encoding="utf-8") == "keep-me"
    assert not record_output.exists()


def test_cases_validate_reports_readable_text_counts() -> None:
    cases_path = ROOT / "examples" / "cases" / "minimal-get.yaml"

    result = runner.invoke(app, ["cases", "validate", "--cases", str(cases_path)])

    assert result.exit_code == 0
    assert "1 case, 1 step, 0 relations" in result.stdout


def test_cases_validate_checks_openapi_semantics() -> None:
    cases_path = ROOT / "examples" / "cases" / "minimal-get.yaml"
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"

    result = runner.invoke(
        app,
        [
            "cases",
            "validate",
            "--cases",
            str(cases_path),
            "--spec",
            str(spec_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["semantic"] is True
    assert output["spec_id"] == "demo-items-v1"


def test_cases_validate_returns_semantic_issues(tmp_path: Path) -> None:
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"
    cases_path = tmp_path / "unknown-operation.yaml"
    source = (ROOT / "examples" / "cases" / "minimal-get.yaml").read_text(encoding="utf-8")
    cases_path.write_text(source.replace("listItems", "missingOperation"), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "cases",
            "validate",
            "--cases",
            str(cases_path),
            "--spec",
            str(spec_path),
            "--json",
        ],
    )

    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["stage"] == "semantic"
    assert output["issues"][0]["path"] == "cases[0].steps[0].operation_id"


def test_cases_validate_rejects_invalid_batch(tmp_path: Path) -> None:
    cases_path = tmp_path / "empty.yaml"
    cases_path.write_text('schema_version: "1.0"\ncases: []\n', encoding="utf-8")

    result = runner.invoke(
        app,
        ["cases", "validate", "--cases", str(cases_path), "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["valid"] is False


def test_cases_run_emits_and_writes_complete_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases_path = ROOT / "examples" / "cases" / "minimal-get.yaml"
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"
    output_path = tmp_path / "run-result.json"

    def fake_execute(*args: object, **kwargs: object) -> RunResult:
        assert args[2] == "https://example.test/api"
        assert kwargs["batch_name"] == "minimal-get"
        return run_result()

    monkeypatch.setattr(
        "openapi_ai_test_evaluator.cli.app.execute_test_case_batch",
        fake_execute,
    )

    result = runner.invoke(
        app,
        [
            "cases",
            "run",
            "--spec",
            str(spec_path),
            "--cases",
            str(cases_path),
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


def test_cases_run_rejects_invalid_base_url() -> None:
    cases_path = ROOT / "examples" / "cases" / "minimal-get.yaml"
    spec_path = ROOT / "examples" / "demo-items" / "openapi.yaml"

    result = runner.invoke(
        app,
        [
            "cases",
            "run",
            "--spec",
            str(spec_path),
            "--cases",
            str(cases_path),
            "--base-url",
            "ftp://example.test/api",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["stage"] == "runner"


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
