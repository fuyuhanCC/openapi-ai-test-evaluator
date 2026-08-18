import pytest
from pydantic import ValidationError

from openapi_ai_test_evaluator.domain import TestPlan as PlanModel


def minimal_plan() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "kind": "TestPlan",
        "metadata": {"name": "minimal-plan", "generator": {"type": "manual"}},
        "target": {"spec_id": "demo-items-v1"},
        "scenarios": [
            {
                "id": "read-item",
                "steps": [{"id": "read", "operation_id": "getItem"}],
            }
        ],
    }


def test_rejects_unknown_fields() -> None:
    raw_plan = minimal_plan()
    raw_plan["unexpected"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PlanModel.model_validate(raw_plan)


def test_rejects_duplicate_scenario_ids() -> None:
    raw_plan = minimal_plan()
    scenarios = raw_plan["scenarios"]
    assert isinstance(scenarios, list)
    scenarios.append({"id": "read-item", "steps": [{"id": "other", "operation_id": "x"}]})

    with pytest.raises(ValidationError, match="scenario IDs must be unique"):
        PlanModel.model_validate(raw_plan)


def test_rejects_variable_reference_with_sibling_keys() -> None:
    raw_plan = minimal_plan()
    scenarios = raw_plan["scenarios"]
    assert isinstance(scenarios, list)
    scenarios[0]["steps"][0]["request"] = {
        "body": {"name": {"$var": "item_name", "fallback": "unsafe ambiguity"}}
    }

    with pytest.raises(ValidationError, match="cannot contain sibling keys"):
        PlanModel.model_validate(raw_plan)


def test_schema_contains_top_level_contract() -> None:
    schema = PlanModel.model_json_schema()

    assert schema["title"] == "TestPlan"
    assert set(schema["required"]) == {
        "schema_version",
        "kind",
        "metadata",
        "target",
        "scenarios",
    }
    assert "schema_mismatch" in schema["$defs"]["ViolationCode"]["enum"]
