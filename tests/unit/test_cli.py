import json
from pathlib import Path

from typer.testing import CliRunner

from openapi_ai_test_evaluator.cli.app import app

ROOT = Path(__file__).parents[2]
runner = CliRunner()


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


def test_plan_validate_returns_nonzero_for_invalid_plan() -> None:
    plan_path = (
        ROOT / "tests" / "fixtures" / "plans" / "invalid" / "invalid-without-violations.yaml"
    )

    result = runner.invoke(app, ["plan", "validate", "--plan", str(plan_path)])

    assert result.exit_code == 1
    assert "Invalid TestPlan" in result.output


def test_plan_schema_writes_generated_schema(tmp_path: Path) -> None:
    output_path = tmp_path / "test-plan.schema.json"

    result = runner.invoke(app, ["plan", "schema", "--out", str(output_path)])

    assert result.exit_code == 0
    schema = json.loads(output_path.read_text(encoding="utf-8"))
    assert schema["title"] == "TestPlan"
