from pathlib import Path

import pytest

from openapi_ai_test_evaluator.validation import PlanLoadError, load_test_plan

ROOT = Path(__file__).parents[2]
VALID_PLAN_DIR = ROOT / "examples" / "plans"
INVALID_PLAN_DIR = ROOT / "tests" / "fixtures" / "plans" / "invalid"


@pytest.mark.parametrize(
    "plan_path",
    sorted(VALID_PLAN_DIR.glob("*.yaml")),
    ids=lambda path: path.stem,
)
def test_loads_reviewed_example_plans(plan_path: Path) -> None:
    plan = load_test_plan(plan_path)

    assert plan.schema_version == "1.0"
    assert plan.scenarios


@pytest.mark.parametrize(
    "plan_path",
    sorted(INVALID_PLAN_DIR.glob("*.yaml")),
    ids=lambda path: path.stem,
)
def test_rejects_invalid_contract_fixtures(plan_path: Path) -> None:
    with pytest.raises(PlanLoadError):
        load_test_plan(plan_path)


def test_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- not\n- a\n- plan\n", encoding="utf-8")

    with pytest.raises(PlanLoadError, match="YAML mapping"):
        load_test_plan(path)
