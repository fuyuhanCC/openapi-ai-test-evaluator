import pytest
from pydantic import ValidationError

from openapi_ai_test_evaluator.domain import TestPlan as PlanModel
from openapi_ai_test_evaluator.domain.test_plan import (
    LIFECYCLE_RELATION_TYPES,
    METAMORPHIC_RELATION_TYPES,
    Assertion,
    RelationKind,
    RelationType,
)


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


def test_relation_types_have_disjoint_explicit_kinds() -> None:
    assert METAMORPHIC_RELATION_TYPES.isdisjoint(LIFECYCLE_RELATION_TYPES)
    assert METAMORPHIC_RELATION_TYPES | LIFECYCLE_RELATION_TYPES == set(RelationType)
    assert RelationType.QUERY_ORDER.kind is RelationKind.METAMORPHIC
    assert RelationType.CREATE_READ.kind is RelationKind.LIFECYCLE


def test_distinguishes_explicit_json_null_from_missing_expected_value() -> None:
    assertion = Assertion.model_validate(
        {
            "operator": "equals",
            "actual": {"source": "response.body", "pointer": "/optional"},
            "expected": None,
        }
    )

    assert assertion.expected is None

    with pytest.raises(ValidationError, match="requires actual and expected"):
        Assertion.model_validate(
            {
                "operator": "equals",
                "actual": {"source": "response.body", "pointer": "/optional"},
            }
        )


def test_enforces_selector_pointer_by_response_source() -> None:
    with pytest.raises(ValidationError, match="response.headers selectors require"):
        Assertion.model_validate(
            {
                "operator": "exists",
                "actual": {"source": "response.headers"},
            }
        )

    with pytest.raises(ValidationError, match="response.status selectors cannot"):
        Assertion.model_validate(
            {
                "operator": "exists",
                "actual": {"source": "response.status", "pointer": "/code"},
            }
        )


@pytest.mark.parametrize(
    ("operator", "expected", "message"),
    [
        ("length_is", -1, "non-negative integer"),
        ("greater_than", True, "numeric expected"),
        ("matches_pattern", "[", "valid regular expression"),
    ],
)
def test_rejects_invalid_operator_specific_expected_values(
    operator: str,
    expected: object,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        Assertion.model_validate(
            {
                "operator": operator,
                "actual": {"source": "response.body", "pointer": ""},
                "expected": expected,
            }
        )
