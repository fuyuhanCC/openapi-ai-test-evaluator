"""Validation helpers for the supported OpenAPI 3.0/3.1 Schema Object subset."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from jsonschema import FormatChecker

from openapi_ai_test_evaluator.domain.openapi import (
    SUPPORTED_STRING_FORMATS,
    SchemaDefinition,
)
from openapi_ai_test_evaluator.domain.test_plan import ViolationCode
from openapi_ai_test_evaluator.spec.loader import resolve_local_ref

FORMAT_CHECKER = FormatChecker()


@dataclass(frozen=True)
class SchemaViolation:
    code: ViolationCode
    field: str
    pointer: str
    message: str


def is_variable_reference(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"$var"}


def variable_references(value: Any) -> set[str]:
    """Collect declarative variable references from a JSON-like value."""
    if is_variable_reference(value):
        variable = value["$var"]
        return {variable} if isinstance(variable, str) else set()
    if isinstance(value, dict):
        references: set[str] = set()
        for nested in value.values():
            references.update(variable_references(nested))
        return references
    if isinstance(value, list):
        references = set()
        for nested in value:
            references.update(variable_references(nested))
        return references
    return set()


def _field_from_pointer(pointer: str) -> str:
    if not pointer:
        return "$body"
    return pointer.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")


def _child_pointer(pointer: str, token: str | int) -> str:
    encoded = str(token).replace("~", "~0").replace("/", "~1")
    return f"{pointer}/{encoded}"


def _resolve_schema(
    schema: SchemaDefinition,
    document: dict[str, Any],
    reference_stack: tuple[str, ...],
) -> tuple[SchemaDefinition, tuple[str, ...]]:
    if isinstance(schema, bool):
        return schema, reference_stack
    reference = schema.get("$ref")
    if not isinstance(reference, str):
        return schema, reference_stack
    if reference in reference_stack:
        return {}, reference_stack
    resolved = resolve_local_ref(document, reference)
    if not isinstance(resolved, (dict, bool)):
        return {}, reference_stack
    resolved_stack = (*reference_stack, reference)
    is_openapi_31 = str(document.get("openapi", "")).startswith("3.1.")
    siblings = {key: value for key, value in schema.items() if key != "$ref"}
    if is_openapi_31 and siblings:
        return {"allOf": [resolved, siblings]}, resolved_stack
    return resolved, resolved_stack


def _matches_json_type(value: Any, expected_type: Any) -> bool:
    if isinstance(expected_type, list):
        return any(_matches_json_type(value, candidate) for candidate in expected_type)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


def _declares_json_type(expected_type: Any, type_name: str) -> bool:
    if isinstance(expected_type, str):
        return expected_type == type_name
    if isinstance(expected_type, list):
        return type_name in expected_type
    return False


def _json_values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if (
        isinstance(left, (int, float))
        and isinstance(right, (int, float))
        and not isinstance(left, bool)
        and not isinstance(right, bool)
    ):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _json_values_equal(left[key], right[key]) for key in left
        )
    return left == right


def _has_duplicate_json_items(values: list[Any]) -> bool:
    return any(
        _json_values_equal(values[left_index], values[right_index])
        for left_index in range(len(values))
        for right_index in range(left_index + 1, len(values))
    )


def _is_multiple_of(value: int | float, divisor: int | float) -> bool:
    try:
        return Decimal(str(value)) % Decimal(str(divisor)) == 0
    except (InvalidOperation, ZeroDivisionError):
        return True


def _is_absolute_uri(value: str) -> bool:
    if any(character.isspace() or ord(character) > 127 for character in value):
        return False
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(parsed.scheme and re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*", parsed.scheme))


def _matches_string_format(value: str, format_name: str) -> bool:
    if format_name == "uri":
        return _is_absolute_uri(value)
    return FORMAT_CHECKER.conforms(value, format_name)


def validate_schema_value(
    value: Any,
    schema: SchemaDefinition,
    document: dict[str, Any],
    *,
    pointer: str = "",
    reference_stack: tuple[str, ...] = (),
) -> list[SchemaViolation]:
    """Validate a concrete value against the supported OpenAPI schema subset."""
    if isinstance(schema, bool):
        if schema:
            return []
        return [
            SchemaViolation(
                code=ViolationCode.SCHEMA_MISMATCH,
                field=_field_from_pointer(pointer),
                pointer=pointer,
                message="false schema rejects every value",
            )
        ]
    if is_variable_reference(value):
        return []

    schema, reference_stack = _resolve_schema(schema, document, reference_stack)
    if isinstance(schema, bool):
        return validate_schema_value(
            value,
            schema,
            document,
            pointer=pointer,
            reference_stack=reference_stack,
        )
    violations: list[SchemaViolation] = []

    if value is None and schema.get("nullable") is True:
        return []

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for branch in all_of:
            if isinstance(branch, (dict, bool)):
                violations.extend(
                    validate_schema_value(
                        value,
                        branch,
                        document,
                        pointer=pointer,
                        reference_stack=reference_stack,
                    )
                )

    for keyword, required_matches in (("anyOf", "at_least_one"), ("oneOf", "exactly_one")):
        branches = schema.get(keyword)
        if not isinstance(branches, list):
            continue
        branch_results = [
            validate_schema_value(
                value,
                branch,
                document,
                pointer=pointer,
                reference_stack=reference_stack,
            )
            for branch in branches
            if isinstance(branch, (dict, bool))
        ]
        match_count = sum(not branch_violations for branch_violations in branch_results)
        matches = match_count >= 1 if required_matches == "at_least_one" else match_count == 1
        if not matches and not (
            keyword == "oneOf" and match_count > 1 and variable_references(value)
        ):
            expectation = "at least one" if keyword == "anyOf" else "exactly one"
            violations.append(
                SchemaViolation(
                    code=ViolationCode.SCHEMA_MISMATCH,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=(
                        f"value must match {expectation} {keyword} branch; matched {match_count}"
                    ),
                )
            )

    expected_type = schema.get("type")
    if not _matches_json_type(value, expected_type):
        return [
            SchemaViolation(
                code=ViolationCode.TYPE_MISMATCH,
                field=_field_from_pointer(pointer),
                pointer=pointer,
                message=f"expected {expected_type}, received {type(value).__name__}",
            )
        ]

    if "const" in schema and not _json_values_equal(value, schema["const"]):
        violations.append(
            SchemaViolation(
                code=ViolationCode.INVALID_ENUM,
                field=_field_from_pointer(pointer),
                pointer=pointer,
                message=f"value {value!r} must equal {schema['const']!r}",
            )
        )

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and not any(
        _json_values_equal(value, candidate) for candidate in enum_values
    ):
        violations.append(
            SchemaViolation(
                code=ViolationCode.INVALID_ENUM,
                field=_field_from_pointer(pointer),
                pointer=pointer,
                message=f"value {value!r} is not one of {enum_values!r}",
            )
        )

    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for field_name in required:
                if isinstance(field_name, str) and field_name not in value:
                    field_pointer = _child_pointer(pointer, field_name)
                    violations.append(
                        SchemaViolation(
                            code=ViolationCode.MISSING_REQUIRED,
                            field=field_name,
                            pointer=field_pointer,
                            message=f"required field {field_name!r} is missing",
                        )
                    )

        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        additional_properties = schema.get("additionalProperties")
        for field_name, field_value in value.items():
            if field_name in properties and isinstance(properties[field_name], (dict, bool)):
                violations.extend(
                    validate_schema_value(
                        field_value,
                        properties[field_name],
                        document,
                        pointer=_child_pointer(pointer, field_name),
                        reference_stack=reference_stack,
                    )
                )
            elif isinstance(additional_properties, dict):
                violations.extend(
                    validate_schema_value(
                        field_value,
                        additional_properties,
                        document,
                        pointer=_child_pointer(pointer, field_name),
                        reference_stack=reference_stack,
                    )
                )
            elif additional_properties is False:
                violations.append(
                    SchemaViolation(
                        code=ViolationCode.ADDITIONAL_PROPERTY,
                        field=field_name,
                        pointer=_child_pointer(pointer, field_name),
                        message=f"additional field {field_name!r} is not allowed",
                    )
                )

        minimum_properties = schema.get("minProperties")
        if isinstance(minimum_properties, int) and len(value) < minimum_properties:
            violations.append(
                SchemaViolation(
                    code=ViolationCode.OUT_OF_RANGE,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"object requires at least {minimum_properties} properties",
                )
            )
        maximum_properties = schema.get("maxProperties")
        if isinstance(maximum_properties, int) and len(value) > maximum_properties:
            violations.append(
                SchemaViolation(
                    code=ViolationCode.OUT_OF_RANGE,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"object allows at most {maximum_properties} properties",
                )
            )

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, (dict, bool)):
            for index, item in enumerate(value):
                violations.extend(
                    validate_schema_value(
                        item,
                        item_schema,
                        document,
                        pointer=_child_pointer(pointer, index),
                        reference_stack=reference_stack,
                    )
                )
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            violations.append(
                SchemaViolation(
                    code=ViolationCode.OUT_OF_RANGE,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"array requires at least {minimum_items} items",
                )
            )
        if isinstance(maximum_items, int) and len(value) > maximum_items:
            violations.append(
                SchemaViolation(
                    code=ViolationCode.OUT_OF_RANGE,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"array allows at most {maximum_items} items",
                )
            )
        if schema.get("uniqueItems") is True and _has_duplicate_json_items(value):
            violations.append(
                SchemaViolation(
                    code=ViolationCode.SCHEMA_MISMATCH,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message="array items must be unique",
                )
            )

    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        maximum_length = schema.get("maxLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            violations.append(
                SchemaViolation(
                    code=ViolationCode.OUT_OF_RANGE,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"string is shorter than {minimum_length}",
                )
            )
        if isinstance(maximum_length, int) and len(value) > maximum_length:
            violations.append(
                SchemaViolation(
                    code=ViolationCode.OUT_OF_RANGE,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"string is longer than {maximum_length}",
                )
            )
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            violations.append(
                SchemaViolation(
                    code=ViolationCode.INVALID_FORMAT,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"string does not match pattern {pattern!r}",
                )
            )
        format_name = schema.get("format")
        if (
            isinstance(format_name, str)
            and format_name in SUPPORTED_STRING_FORMATS
            and not _matches_string_format(value, format_name)
        ):
            violations.append(
                SchemaViolation(
                    code=ViolationCode.INVALID_FORMAT,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"string does not match format {format_name!r}",
                )
            )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        multiple_of = schema.get("multipleOf")
        exclusive_minimum = schema.get("exclusiveMinimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        is_openapi_31 = str(document.get("openapi", "")).startswith("3.1.")

        if (
            isinstance(multiple_of, (int, float))
            and not isinstance(multiple_of, bool)
            and not _is_multiple_of(value, multiple_of)
        ):
            violations.append(
                SchemaViolation(
                    code=ViolationCode.OUT_OF_RANGE,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"value must be a multiple of {multiple_of}",
                )
            )

        minimum_violated = (
            isinstance(minimum, (int, float))
            and not isinstance(minimum, bool)
            and (
                value <= minimum
                if not is_openapi_31 and exclusive_minimum is True
                else value < minimum
            )
        )
        if minimum_violated:
            violations.append(
                SchemaViolation(
                    code=ViolationCode.OUT_OF_RANGE,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"value violates minimum {minimum}",
                )
            )

        maximum_violated = (
            isinstance(maximum, (int, float))
            and not isinstance(maximum, bool)
            and (
                value >= maximum
                if not is_openapi_31 and exclusive_maximum is True
                else value > maximum
            )
        )
        if maximum_violated:
            violations.append(
                SchemaViolation(
                    code=ViolationCode.OUT_OF_RANGE,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"value violates maximum {maximum}",
                )
            )

        if (
            is_openapi_31
            and isinstance(exclusive_minimum, (int, float))
            and not isinstance(exclusive_minimum, bool)
            and value <= exclusive_minimum
        ):
            violations.append(
                SchemaViolation(
                    code=ViolationCode.OUT_OF_RANGE,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"value must be greater than {exclusive_minimum}",
                )
            )
        if (
            is_openapi_31
            and isinstance(exclusive_maximum, (int, float))
            and not isinstance(exclusive_maximum, bool)
            and value >= exclusive_maximum
        ):
            violations.append(
                SchemaViolation(
                    code=ViolationCode.OUT_OF_RANGE,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"value must be less than {exclusive_maximum}",
                )
            )

    return violations


def schema_at_pointer(
    schema: SchemaDefinition,
    pointer: str,
    document: dict[str, Any],
    *,
    reference_stack: tuple[str, ...] = (),
) -> SchemaDefinition | None:
    """Return the schema addressed by a response JSON pointer."""
    schema, reference_stack = _resolve_schema(schema, document, reference_stack)
    if pointer == "":
        return schema
    if isinstance(schema, bool):
        return None
    if not pointer.startswith("/"):
        return None
    tokens = [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]
    return _schema_at_tokens(schema, tokens, document, reference_stack)


def _schema_at_tokens(
    schema: SchemaDefinition,
    tokens: list[str],
    document: dict[str, Any],
    reference_stack: tuple[str, ...],
) -> SchemaDefinition | None:
    schema, reference_stack = _resolve_schema(schema, document, reference_stack)
    if not tokens:
        return schema
    if isinstance(schema, bool):
        return None

    for keyword in ("allOf", "oneOf", "anyOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, (dict, bool)):
                    found = _schema_at_tokens(branch, tokens, document, reference_stack)
                    if found is not None:
                        return found

    token, *remaining = tokens
    properties = schema.get("properties")
    if isinstance(properties, dict) and isinstance(properties.get(token), (dict, bool)):
        return _schema_at_tokens(properties[token], remaining, document, reference_stack)

    if _declares_json_type(schema.get("type"), "array") and token.isdigit():
        item_schema = schema.get("items")
        if isinstance(item_schema, (dict, bool)):
            return _schema_at_tokens(item_schema, remaining, document, reference_stack)
    return None
