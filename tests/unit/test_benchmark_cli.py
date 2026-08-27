import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from openapi_ai_test_evaluator.cli.app import app
from openapi_ai_test_evaluator.domain.execution import ExecutionOutcome
from openapi_ai_test_evaluator.domain.generation import AdaptationRecord
from openapi_ai_test_evaluator.evaluation import EvaluatedSuiteArtifactPaths

ROOT = Path(__file__).parents[2]
runner = CliRunner()


def write_source_record(path: Path) -> None:
    record = AdaptationRecord(
        schema_version="1.0",
        kind="AdaptationRecord",
        tool="schemathesis",
        tool_version="4.25.2",
        adapter_version="schemathesis-case-v1",
        seed=7,
        duration_ms=2,
        received_case_count=1,
        adapted_case_count=1,
        rejected_case_count=0,
        skip_reasons=[],
    )
    path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")


def command_arguments(
    source_record: Path,
    output_directory: Path,
    composition_record: Path | None = None,
) -> list[str]:
    arguments = [
        "benchmark",
        "run-suite",
        "--spec",
        str(ROOT / "examples" / "demo-items" / "openapi.yaml"),
        "--cases",
        str(ROOT / "examples" / "cases" / "minimal-get.yaml"),
        "--source-record",
        str(source_record),
        "--suite-id",
        "schemathesis",
        "--repetition",
        "2",
        "--runner-base-url",
        "http://proxy.test",
        "--proxy-control-url",
        "http://proxy.test",
        "--sut-reset-url",
        "http://sut.test/__test__/reset",
        "--fault",
        "status-fault",
        "--fault",
        "missing-field-fault",
        "--output-directory",
        str(output_directory),
        "--json",
    ]
    if composition_record is not None:
        arguments.extend(["--composition-record", str(composition_record)])
    return arguments


def test_run_suite_connects_validated_inputs_execution_and_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_record = tmp_path / "adaptation.json"
    output_directory = tmp_path / "schemathesis-r2"
    write_source_record(source_record)
    captured: dict[str, object] = {}

    def fake_run(batch: object, spec: object, record: object, **kwargs: object) -> object:
        captured["batch"] = batch
        captured["spec"] = spec
        captured["record"] = record
        captured.update(kwargs)
        return SimpleNamespace(
            execution=SimpleNamespace(clean=SimpleNamespace(outcome=ExecutionOutcome.PASSED)),
            evaluation=SimpleNamespace(
                evaluation_id="evaluation-schemathesis-r2",
                suite_id="schemathesis",
                repetition=2,
                fault_summary=SimpleNamespace(
                    configured_fault_count=2,
                    detected_fault_count=1,
                ),
                admission=SimpleNamespace(admitted_case_count=1),
                composition=None,
                execution=SimpleNamespace(admitted_case_count=1),
            ),
        )

    def fake_write(
        evaluated: object,
        destination: Path,
        *,
        overwrite: bool,
    ) -> EvaluatedSuiteArtifactPaths:
        captured["evaluated"] = evaluated
        captured["destination"] = destination
        captured["overwrite"] = overwrite
        return EvaluatedSuiteArtifactPaths(
            clean_run=destination / "execution" / "clean.json",
            fault_runs=(),
            evaluation=destination / "evaluation" / "evaluation.json",
        )

    monkeypatch.setattr(
        "openapi_ai_test_evaluator.cli.app.run_evaluated_suite",
        fake_run,
    )
    monkeypatch.setattr(
        "openapi_ai_test_evaluator.cli.app.write_evaluated_suite_artifacts",
        fake_write,
    )

    result = runner.invoke(app, command_arguments(source_record, output_directory))

    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert summary == {
        "status": "completed",
        "evaluation_id": "evaluation-schemathesis-r2",
        "suite_id": "schemathesis",
        "repetition": 2,
        "clean_outcome": "passed",
        "configured_faults": 2,
        "detected_faults": 1,
        "native_cases": 1,
        "enhancement_cases": 0,
        "executed_cases": 1,
        "output_directory": str(output_directory),
        "evaluation_output": str(output_directory / "evaluation" / "evaluation.json"),
    }
    assert captured["fault_ids"] == ["status-fault", "missing-field-fault"]
    assert captured["evaluation_id"] == "evaluation-schemathesis-r2"
    assert captured["composition_record"] is None
    assert captured["destination"] == output_directory
    assert captured["overwrite"] is False


def test_run_suite_rejects_invalid_source_record_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_record = tmp_path / "invalid.json"
    source_record.write_text("{}\n", encoding="utf-8")
    executed = False

    def fake_run(*args: object, **kwargs: object) -> object:
        nonlocal executed
        executed = True
        raise AssertionError("execution must not start")

    monkeypatch.setattr(
        "openapi_ai_test_evaluator.cli.app.run_evaluated_suite",
        fake_run,
    )

    result = runner.invoke(
        app,
        command_arguments(source_record, tmp_path / "schemathesis-r2"),
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["stage"] == "source-record"
    assert executed is False


def test_run_suite_rejects_invalid_composition_record_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_record = tmp_path / "adaptation.json"
    composition_record = tmp_path / "composition.json"
    write_source_record(source_record)
    composition_record.write_text("{}\n", encoding="utf-8")
    executed = False

    def fake_run(*args: object, **kwargs: object) -> object:
        nonlocal executed
        executed = True
        raise AssertionError("execution must not start")

    monkeypatch.setattr(
        "openapi_ai_test_evaluator.cli.app.run_evaluated_suite",
        fake_run,
    )

    result = runner.invoke(
        app,
        command_arguments(
            source_record,
            tmp_path / "schemathesis-enhanced-r2",
            composition_record,
        ),
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["stage"] == "composition-record"
    assert executed is False


def test_run_suite_refuses_existing_output_directory_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_record = tmp_path / "adaptation.json"
    output_directory = tmp_path / "schemathesis-r2"
    write_source_record(source_record)
    output_directory.mkdir()
    executed = False

    def fake_run(*args: object, **kwargs: object) -> object:
        nonlocal executed
        executed = True
        raise AssertionError("execution must not start")

    monkeypatch.setattr(
        "openapi_ai_test_evaluator.cli.app.run_evaluated_suite",
        fake_run,
    )

    result = runner.invoke(app, command_arguments(source_record, output_directory))

    assert result.exit_code == 1
    assert json.loads(result.stdout)["stage"] == "artifacts"
    assert executed is False
