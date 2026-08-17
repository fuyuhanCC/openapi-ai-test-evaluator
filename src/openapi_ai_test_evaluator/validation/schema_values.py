"""Validation helpers for the supported OpenAPI 3.0 Schema Object subset."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openapi_ai_test_evaluator.domain.test_plan import ViolationCode
from openapi_ai_test_evaluator.spec.loader import resolve_local_ref


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
    schema: dict[str, Any],
    document: dict[str, Any],
    reference_stack: tuple[str, ...],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    reference = schema.get("$ref")
    if not isinstance(reference, str):
        return schema, reference_stack
    if reference in reference_stack:
        return {}, reference_stack
    resolved = resolve_local_ref(document, reference)
    if not isinstance(resolved, dict):
        return {}, reference_stack
    return resolved, (*reference_stack, reference)


def validate_schema_value(
    value: Any,
    schema: dict[str, Any],
    document: dict[str, Any],
    *,
    pointer: str = "",
    reference_stack: tuple[str, ...] = (),
) -> list[SchemaViolation]:
    """Validate a concrete value against the supported OpenAPI schema subset."""
    if is_variable_reference(value):
        return []

    schema, reference_stack = _resolve_schema(schema, document, reference_stack)
    violations: list[SchemaViolation] = []

    if value is None and schema.get("nullable") is True:
        return []

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for branch in all_of:
            if isinstance(branch, dict):
                violations.extend(
                    validate_schema_value(
                        value,
                        branch,
                        document,
                        pointer=pointer,
                        reference_stack=reference_stack,
                    )
                )
        return violations

    expected_type = schema.get("type")
    type_matches = True
    if expected_type == "object":
        type_matches = isinstance(value, dict)
    elif expected_type == "array":
        type_matches = isinstance(value, list)
    elif expected_type == "string":
        type_matches = isinstance(value, str)
    elif expected_type == "integer":
        type_matches = isinstance(value, int) and not isinstance(value, bool)
    elif expected_type == "number":
        type_matches = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected_type == "boolean":
        type_matches = isinstance(value, bool)

    if not type_matches:
        return [
            SchemaViolation(
                code=ViolationCode.TYPE_MISMATCH,
                field=_field_from_pointer(pointer),
                pointer=pointer,
                message=f"expected {expected_type}, received {type(value).__name__}",
            )
        ]

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
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
        for field_name, field_value in value.items():
            if field_name in properties and isinstance(properties[field_name], dict):
                violations.extend(
                    validate_schema_value(
                        field_value,
                        properties[field_name],
                        document,
                        pointer=_child_pointer(pointer, field_name),
                        reference_stack=reference_stack,
                    )
                )
            elif schema.get("additionalProperties") is False:
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

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
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

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            violations.append(
                SchemaViolation(
                    code=ViolationCode.OUT_OF_RANGE,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"value is below minimum {minimum}",
                )
            )
        if isinstance(maximum, (int, float)) and value > maximum:
            violations.append(
                SchemaViolation(
                    code=ViolationCode.OUT_OF_RANGE,
                    field=_field_from_pointer(pointer),
                    pointer=pointer,
                    message=f"value is above maximum {maximum}",
                )
            )

    return violations


def schema_at_pointer(
    schema: dict[str, Any],
    pointer: str,
    document: dict[str, Any],
    *,
    reference_stack: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """Return the schema addressed by a response JSON pointer."""
    schema, reference_stack = _resolve_schema(schema, document, reference_stack)
    if pointer == "":
        return schema
    if not pointer.startswith("/"):
        return None
    tokens = [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]
    return _schema_at_tokens(schema, tokens, document, reference_stack)


def _schema_at_tokens(
    schema: dict[str, Any],
    tokens: list[str],
    document: dict[str, Any],
    reference_stack: tuple[str, ...],
) -> dict[str, Any] | None:
    schema, reference_stack = _resolve_schema(schema, document, reference_stack)
    if not tokens:
        return schema

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for branch in all_of:
            if isinstance(branch, dict):
                found = _schema_at_tokens(branch, tokens, document, reference_stack)
                if found is not None:
                    return found
        return None

    token, *remaining = tokens
    properties = schema.get("properties")
    if isinstance(properties, dict) and isinstance(properties.get(token), dict):
        return _schema_at_tokens(properties[token], remaining, document, reference_stack)

    if schema.get("type") == "array" and token.isdigit():
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return _schema_at_tokens(item_schema, remaining, document, reference_stack)
    return None
