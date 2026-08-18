from pathlib import Path

import pytest

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


def test_enforces_array_length_and_unique_items() -> None:
    schema = {
        "type": "array",
        "minItems": 2,
        "maxItems": 3,
        "uniqueItems": True,
    }

    too_short = validate_schema_value([1], schema, SPEC.document)
    too_long = validate_schema_value([1, 2, 3, 4], schema, SPEC.document)
    duplicates = validate_schema_value(
        [{"id": 1}, {"id": 1}],
        schema,
        SPEC.document,
    )

    assert [issue.code for issue in too_short] == [ViolationCode.OUT_OF_RANGE]
    assert [issue.code for issue in too_long] == [ViolationCode.OUT_OF_RANGE]
    assert [issue.code for issue in duplicates] == [ViolationCode.SCHEMA_MISMATCH]
    assert validate_schema_value([True, 1], schema, SPEC.document) == []


def test_enforces_max_properties_and_additional_property_schema() -> None:
    schema = {
        "type": "object",
        "maxProperties": 2,
        "properties": {"name": {"type": "string"}},
        "additionalProperties": {"type": "integer", "minimum": 1},
    }

    assert validate_schema_value({"name": "item", "count": 2}, schema, SPEC.document) == []

    invalid_extra = validate_schema_value(
        {"name": "item", "count": "two"},
        schema,
        SPEC.document,
    )
    too_many = validate_schema_value(
        {"name": "item", "count": 2, "rank": 3},
        schema,
        SPEC.document,
    )

    assert [issue.code for issue in invalid_extra] == [ViolationCode.TYPE_MISMATCH]
    assert invalid_extra[0].pointer == "/count"
    assert [issue.code for issue in too_many] == [ViolationCode.OUT_OF_RANGE]


def test_accepts_nullable_and_runtime_variables() -> None:
    schema = {"type": "integer"}

    assert validate_schema_value({"$var": "item_id"}, schema, SPEC.document) == []
    assert validate_schema_value(None, {**schema, "nullable": True}, SPEC.document) == []


def test_supports_openapi_31_type_arrays_and_null() -> None:
    schema = {"type": ["string", "null"]}

    assert validate_schema_value("hello", schema, SPEC.document) == []
    assert validate_schema_value(None, schema, SPEC.document) == []

    violations = validate_schema_value(123, schema, SPEC.document)
    assert [violation.code for violation in violations] == [ViolationCode.TYPE_MISMATCH]


def test_boolean_schemas_apply_at_top_level_and_nested_values() -> None:
    assert validate_schema_value("anything", True, SPEC.document) == []

    rejected = validate_schema_value("anything", False, SPEC.document)
    rejected_variable = validate_schema_value(
        {"$var": "runtime_value"},
        False,
        SPEC.document,
    )
    rejected_item = validate_schema_value(
        [1],
        {"type": "array", "items": False},
        SPEC.document,
    )
    rejected_property = validate_schema_value(
        {"forbidden": 1},
        {"type": "object", "properties": {"forbidden": False}},
        SPEC.document,
    )

    assert [issue.code for issue in rejected] == [ViolationCode.SCHEMA_MISMATCH]
    assert [issue.code for issue in rejected_variable] == [ViolationCode.SCHEMA_MISMATCH]
    assert rejected_item[0].pointer == "/0"
    assert rejected_property[0].pointer == "/forbidden"
    assert (
        validate_schema_value(
            "value",
            {"allOf": [True, {"type": "string"}]},
            SPEC.document,
        )
        == []
    )
    assert (
        validate_schema_value(
            "value",
            {"oneOf": [False, {"type": "string"}]},
            SPEC.document,
        )
        == []
    )

    document = {
        "openapi": "3.1.0",
        "components": {"schemas": {"Never": False}},
    }
    rejected_reference = validate_schema_value(
        "value",
        {"$ref": "#/components/schemas/Never"},
        document,
    )
    assert [issue.code for issue in rejected_reference] == [ViolationCode.SCHEMA_MISMATCH]


def test_boolean_schema_pointer_semantics() -> None:
    assert schema_at_pointer(True, "", SPEC.document) is True
    assert schema_at_pointer(False, "", SPEC.document) is False
    assert schema_at_pointer(True, "/unknown", SPEC.document) is None


def test_enforces_openapi_31_const_and_numeric_exclusive_bounds() -> None:
    document = {"openapi": "3.1.0"}

    assert validate_schema_value("ready", {"const": "ready"}, document) == []
    const_issues = validate_schema_value("pending", {"const": "ready"}, document)
    lower_issues = validate_schema_value(
        1,
        {"type": "number", "exclusiveMinimum": 1},
        document,
    )
    upper_issues = validate_schema_value(
        5,
        {"type": "number", "exclusiveMaximum": 5},
        document,
    )

    assert [issue.code for issue in const_issues] == [ViolationCode.INVALID_ENUM]
    assert [issue.code for issue in lower_issues] == [ViolationCode.OUT_OF_RANGE]
    assert [issue.code for issue in upper_issues] == [ViolationCode.OUT_OF_RANGE]
    assert (
        validate_schema_value(
            3,
            {"type": "number", "exclusiveMinimum": 1, "exclusiveMaximum": 5},
            document,
        )
        == []
    )


def test_const_and_enum_use_json_value_equality() -> None:
    const_issues = validate_schema_value(True, {"const": 1}, SPEC.document)
    enum_issues = validate_schema_value(True, {"enum": [1]}, SPEC.document)

    assert [issue.code for issue in const_issues] == [ViolationCode.INVALID_ENUM]
    assert [issue.code for issue in enum_issues] == [ViolationCode.INVALID_ENUM]


def test_enforces_multiple_of_without_float_rounding_errors() -> None:
    schema = {"type": "number", "multipleOf": 0.1}

    assert validate_schema_value(0.3, schema, SPEC.document) == []

    violations = validate_schema_value(0.31, schema, SPEC.document)
    assert [issue.code for issue in violations] == [ViolationCode.OUT_OF_RANGE]


@pytest.mark.parametrize(
    ("format_name", "valid_value", "invalid_value"),
    [
        ("date", "2026-08-17", "2026-02-30"),
        ("date-time", "2026-08-17T10:30:00Z", "2026-08-17"),
        ("uuid", "123e4567-e89b-12d3-a456-426614174000", "not-a-uuid"),
        ("email", "user@example.com", "missing-at.example.com"),
        ("uri", "https://example.com/items/1", "not a uri"),
        ("ipv4", "192.0.2.1", "999.0.2.1"),
        ("ipv6", "2001:db8::1", "2001:not-ipv6::1"),
    ],
)
def test_enforces_common_string_formats(
    format_name: str,
    valid_value: str,
    invalid_value: str,
) -> None:
    schema = {"type": "string", "format": format_name}

    assert validate_schema_value(valid_value, schema, SPEC.document) == []

    violations = validate_schema_value(invalid_value, schema, SPEC.document)
    assert [issue.code for issue in violations] == [ViolationCode.INVALID_FORMAT]


def test_enforces_openapi_30_boolean_exclusive_bounds() -> None:
    schema = {
        "type": "number",
        "minimum": 1,
        "exclusiveMinimum": True,
        "maximum": 5,
        "exclusiveMaximum": True,
    }

    lower_issues = validate_schema_value(1, schema, SPEC.document)
    upper_issues = validate_schema_value(5, schema, SPEC.document)

    assert [issue.code for issue in lower_issues] == [ViolationCode.OUT_OF_RANGE]
    assert [issue.code for issue in upper_issues] == [ViolationCode.OUT_OF_RANGE]
    assert validate_schema_value(3, schema, SPEC.document) == []


def test_applies_openapi_31_schema_ref_siblings() -> None:
    document = {
        "openapi": "3.1.0",
        "components": {
            "schemas": {
                "PositiveInteger": {"type": "integer", "minimum": 1},
            }
        },
    }
    schema = {
        "$ref": "#/components/schemas/PositiveInteger",
        "maximum": 5,
    }

    lower_issues = validate_schema_value(0, schema, document)
    upper_issues = validate_schema_value(6, schema, document)

    assert [issue.code for issue in lower_issues] == [ViolationCode.OUT_OF_RANGE]
    assert [issue.code for issue in upper_issues] == [ViolationCode.OUT_OF_RANGE]
    assert validate_schema_value(3, schema, document) == []


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


def test_navigates_nullable_openapi_31_array_by_json_pointer() -> None:
    schema = {
        "type": ["array", "null"],
        "items": {"type": "string"},
    }

    assert schema_at_pointer(schema, "/0", SPEC.document) == {"type": "string"}


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


def test_any_of_requires_at_least_one_matching_branch() -> None:
    schema = {
        "anyOf": [
            {"type": "string", "minLength": 3},
            {"type": "integer", "minimum": 1},
        ]
    }

    assert validate_schema_value("valid", schema, SPEC.document) == []
    assert validate_schema_value(2, schema, SPEC.document) == []

    violations = validate_schema_value(False, schema, SPEC.document)
    assert [violation.code for violation in violations] == [ViolationCode.SCHEMA_MISMATCH]


def test_one_of_requires_exactly_one_branch_and_applies_siblings() -> None:
    schema = {
        "oneOf": [
            {"type": "integer"},
            {"type": "number"},
        ],
        "maximum": 5,
    }

    assert validate_schema_value(1.5, schema, SPEC.document) == []

    overlapping = validate_schema_value(1, schema, SPEC.document)
    no_match = validate_schema_value("one", schema, SPEC.document)
    sibling_violation = validate_schema_value(6.5, schema, SPEC.document)

    assert [issue.code for issue in overlapping] == [ViolationCode.SCHEMA_MISMATCH]
    assert [issue.code for issue in no_match] == [ViolationCode.SCHEMA_MISMATCH]
    assert [issue.code for issue in sibling_violation] == [ViolationCode.OUT_OF_RANGE]

    runtime_value_schema = {
        "oneOf": [
            {"properties": {"kind": {"const": "created"}}},
            {"properties": {"kind": {"const": "updated"}}},
        ]
    }
    assert (
        validate_schema_value(
            {"kind": {"$var": "runtime_kind"}},
            runtime_value_schema,
            SPEC.document,
        )
        == []
    )
