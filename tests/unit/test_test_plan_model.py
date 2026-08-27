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


def test_preserves_absent_body_and_explicit_json_null_during_serialization() -> None:
    absent = PlanModel.model_validate(minimal_plan())
    explicit_null_data = minimal_plan()
    scenarios = explicit_null_data["scenarios"]
    assert isinstance(scenarios, list)
    scenarios[0]["steps"][0]["request"] = {"body": None}
    explicit_null = PlanModel.model_validate(explicit_null_data)

    absent_request = absent.model_dump(mode="json")["scenarios"][0]["steps"][0]["request"]
    null_request = explicit_null.model_dump(mode="json")["scenarios"][0]["steps"][0]["request"]

    assert "body" not in absent_request
    assert null_request["body"] is None
    assert absent.scenarios[0].steps[0].request.body_present is False
    assert explicit_null.scenarios[0].steps[0].request.body_present is True


def test_accepts_a_non_empty_unique_status_set() -> None:
    assertion = Assertion.model_validate(
        {
            "operator": "status_in",
            "expected": [400, 422],
        }
    )

    assert assertion.expected == [400, 422]


@pytest.mark.parametrize(
    ("expected", "message"),
    [
        ([], "non-empty list"),
        ([400, True], "integer status codes"),
        ([400, 400], "must be unique"),
    ],
)
def test_rejects_invalid_status_sets(expected: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Assertion.model_validate({"operator": "status_in", "expected": expected})


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


@pytest.mark.parametrize("expected", [None, 1, "id", "/bad~pointer"])
def test_rejects_invalid_collection_uniqueness_key_pointer(expected: object) -> None:
    with pytest.raises(ValidationError, match="JSON Pointer expected value"):
        Assertion.model_validate(
            {
                "operator": "items_unique_by",
                "actual": {"source": "response.body", "pointer": "/items"},
                "expected": expected,
            }
        )


def test_collection_uniqueness_requires_a_response_body_selector() -> None:
    with pytest.raises(ValidationError, match="response.body selector"):
        Assertion.model_validate(
            {
                "operator": "items_unique_by",
                "actual": {"source": "response.headers", "pointer": "/x-items"},
                "expected": "/id",
            }
        )
