from pathlib import Path

from openapi_ai_test_evaluator.domain.test_plan import ViolationCode
from openapi_ai_test_evaluator.spec import load_openapi
from openapi_ai_test_evaluator.validation.schema_values import (
    schema_at_pointer,
    validate_schema_value,
    variable_references,
)

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")


def test_collects_nested_variable_references() -> None:
    value = {
        "direct": {"$var": "item_id"},
        "nested": [{"$var": "status"}, {"literal": True}],
    }

    assert variable_references(value) == {"item_id", "status"}
    assert variable_references("literal") == set()


def test_reports_supported_object_and_scalar_violations() -> None:
    schema = {
        "type": "object",
        "required": ["name", "count"],
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 3, "pattern": "^[A-Z]"},
            "count": {"type": "integer", "minimum": 1, "maximum": 5},
            "state": {"type": "string", "enum": ["on", "off"]},
        },
    }

    violations = validate_schema_value(
        {"name": "ab", "count": 8, "state": "unknown", "extra": True},
        schema,
        SPEC.document,
    )

    assert {violation.code for violation in violations} == {
        ViolationCode.OUT_OF_RANGE,
        ViolationCode.INVALID_FORMAT,
        ViolationCode.INVALID_ENUM,
        ViolationCode.ADDITIONAL_PROPERTY,
    }


def test_reports_type_missing_required_and_array_item_violations() -> None:
    array_schema = {"type": "array", "items": {"type": "integer"}}
    object_schema = {
        "type": "object",
        "required": ["requiredField"],
        "minProperties": 2,
    }

    array_issues = validate_schema_value([1, "two"], array_schema, SPEC.document)
    object_issues = validate_schema_value({}, object_schema, SPEC.document)
    type_issues = validate_schema_value("not-an-object", object_schema, SPEC.document)

    assert array_issues[0].code is ViolationCode.TYPE_MISMATCH
    assert {issue.code for issue in object_issues} == {
        ViolationCode.MISSING_REQUIRED,
        ViolationCode.OUT_OF_RANGE,
    }
    assert type_issues[0].code is ViolationCode.TYPE_MISMATCH


def test_accepts_nullable_and_runtime_variables() -> None:
    schema = {"type": "integer"}

    assert validate_schema_value({"$var": "item_id"}, schema, SPEC.document) == []
    assert validate_schema_value(None, {**schema, "nullable": True}, SPEC.document) == []


def test_navigates_references_objects_and_arrays_by_json_pointer() -> None:
    item_list = {"$ref": "#/components/schemas/ItemList"}

    item_id = schema_at_pointer(item_list, "/items/0/id", SPEC.document)

    assert item_id is not None
    assert item_id["type"] == "integer"
    assert schema_at_pointer(item_list, "items/0/id", SPEC.document) is None
    assert schema_at_pointer(item_list, "/items/not-an-index", SPEC.document) is None
    assert (
        schema_at_pointer(item_list, "", SPEC.document)
        == SPEC.document["components"]["schemas"]["ItemList"]
    )


def test_accepts_complete_item_response_without_additional_property_errors() -> None:
    response_schema = SPEC.operations["getItem"].responses["200"].schema_definition
    assert response_schema is not None
    response = {
        "id": 42,
        "name": "Test Book",
        "price": 10,
        "status": "active",
        "category": "book",
        "createdAt": "2026-08-17T10:00:00Z",
        "updatedAt": "2026-08-17T10:00:00Z",
    }

    assert validate_schema_value(response, response_schema, SPEC.document) == []


def test_all_of_requires_every_branch_to_match() -> None:
    schema = {
        "allOf": [
            {"type": "integer", "minimum": 1},
            {"type": "integer", "maximum": 5},
        ]
    }

    assert validate_schema_value(3, schema, SPEC.document) == []
    violations = validate_schema_value(8, schema, SPEC.document)
    assert [violation.code for violation in violations] == [ViolationCode.OUT_OF_RANGE]
