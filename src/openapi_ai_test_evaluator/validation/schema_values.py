"""OpenAPI Schema validation adapters and TestPlan-specific schema helpers."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from jsonschema import FormatChecker
from jsonschema.exceptions import ValidationError
from openapi_schema_validator import OAS30Validator, OAS31Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4, DRAFT202012

from openapi_ai_test_evaluator.domain.openapi import SchemaDefinition
from openapi_ai_test_evaluator.domain.test_plan import ViolationCode
from openapi_ai_test_evaluator.spec.loader import resolve_local_ref

_DOCUMENT_URI = "urn:oate:openapi-document"


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
    """Resolve one schema reference for TestPlan pointer navigation."""
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


def _declares_json_type(expected_type: Any, type_name: str) -> bool:
    if isinstance(expected_type, str):
        return expected_type == type_name
    if isinstance(expected_type, list):
        return type_name in expected_type
    return False


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


FORMAT_CHECKER = FormatChecker()
FORMAT_CHECKER.checks("uri")(_is_absolute_uri)


def _with_absolute_local_refs(value: Any) -> Any:
    """Point schema-local references at the registered full OpenAPI document."""
    if isinstance(value, dict):
        rewritten = {key: _with_absolute_local_refs(nested) for key, nested in value.items()}
        reference = rewritten.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/"):
            rewritten["$ref"] = f"{_DOCUMENT_URI}{reference}"
        return rewritten
    if isinstance(value, list):
        return [_with_absolute_local_refs(nested) for nested in value]
    return value


def _with_locatable_boolean_schemas(schema: SchemaDefinition) -> SchemaDefinition:
    """Express 3.1 boolean schemas so nested library errors retain instance paths."""
    if schema is True:
        return {}
    if schema is False:
        return {"not": {}}

    rewritten = copy.deepcopy(schema)
    for keyword in (
        "contains",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    ):
        nested = rewritten.get(keyword)
        if isinstance(nested, (dict, bool)):
            rewritten[keyword] = _with_locatable_boolean_schemas(nested)

    additional_properties = rewritten.get("additionalProperties")
    if isinstance(additional_properties, dict):
        rewritten["additionalProperties"] = _with_locatable_boolean_schemas(additional_properties)

    for keyword in (
        "$defs",
        "definitions",
        "dependentSchemas",
        "patternProperties",
        "properties",
    ):
        nested_schemas = rewritten.get(keyword)
        if isinstance(nested_schemas, dict):
            rewritten[keyword] = {
                name: _with_locatable_boolean_schemas(nested)
                for name, nested in nested_schemas.items()
                if isinstance(nested, (dict, bool))
            }

    for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
        nested_schemas = rewritten.get(keyword)
        if isinstance(nested_schemas, list):
            rewritten[keyword] = [
                _with_locatable_boolean_schemas(nested)
                if isinstance(nested, (dict, bool))
                else nested
                for nested in nested_schemas
            ]
    return rewritten


def _with_locatable_component_schemas(document: dict[str, Any]) -> dict[str, Any]:
    rewritten = copy.deepcopy(document)
    components = rewritten.get("components")
    if not isinstance(components, dict):
        return rewritten
    schemas = components.get("schemas")
    if not isinstance(schemas, dict):
        return rewritten
    components["schemas"] = {
        name: _with_locatable_boolean_schemas(schema)
        if isinstance(schema, (dict, bool))
        else schema
        for name, schema in schemas.items()
    }
    return rewritten


def _with_exact_floats(value: Any) -> Any:
    """Avoid binary-float false positives for JSON Schema multipleOf."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _with_exact_floats(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_with_exact_floats(nested) for nested in value]
    return value


def _variable_paths(value: Any, path: tuple[Any, ...] = ()) -> set[tuple[Any, ...]]:
    if is_variable_reference(value):
        return {path}
    paths: set[tuple[Any, ...]] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            paths.update(_variable_paths(nested, (*path, key)))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            paths.update(_variable_paths(nested, (*path, index)))
    return paths


def _path_is_within(path: tuple[Any, ...], parent: tuple[Any, ...]) -> bool:
    return path[: len(parent)] == parent


def _depends_only_on_runtime_variables(
    error: ValidationError,
    variable_paths: set[tuple[Any, ...]],
) -> bool:
    """Ignore errors that cannot be decided until TestPlan variables are resolved."""
    if error.schema is False or (error.validator == "not" and error.validator_value == {}):
        return False
    error_path = tuple(error.absolute_path)
    if any(_path_is_within(error_path, variable_path) for variable_path in variable_paths):
        return True
    if error.context:
        return all(
            _depends_only_on_runtime_variables(child, variable_paths) for child in error.context
        )
    return error.validator == "oneOf" and any(
        _path_is_within(variable_path, error_path) for variable_path in variable_paths
    )


def _violation_code(keyword: str | None) -> ViolationCode:
    if keyword == "type":
        return ViolationCode.TYPE_MISMATCH
    if keyword == "required":
        return ViolationCode.MISSING_REQUIRED
    if keyword in {"enum", "const"}:
        return ViolationCode.INVALID_ENUM
    if keyword in {
        "exclusiveMaximum",
        "exclusiveMinimum",
        "maximum",
        "maxItems",
        "maxLength",
        "maxProperties",
        "minimum",
        "minItems",
        "minLength",
        "minProperties",
        "multipleOf",
    }:
        return ViolationCode.OUT_OF_RANGE
    if keyword in {"format", "pattern"}:
        return ViolationCode.INVALID_FORMAT
    if keyword == "additionalProperties":
        return ViolationCode.ADDITIONAL_PROPERTY
    return ViolationCode.SCHEMA_MISMATCH


def _missing_required_field(error: ValidationError) -> str | None:
    if not isinstance(error.instance, dict) or not isinstance(error.validator_value, list):
        return None
    for name in error.validator_value:
        if name not in error.instance and error.message == f"{name!r} is a required property":
            return str(name)
    return None


def _additional_fields(error: ValidationError) -> list[str]:
    if not isinstance(error.instance, dict) or not isinstance(error.schema, dict):
        return []
    properties = error.schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    return sorted(str(name) for name in error.instance if name not in properties)


def _error_violations(error: ValidationError, pointer: str) -> list[SchemaViolation]:
    error_pointer = pointer
    for token in error.absolute_path:
        error_pointer = _child_pointer(error_pointer, token)

    fields: list[str] = []
    if error.validator == "required":
        missing = _missing_required_field(error)
        if missing is not None:
            fields = [missing]
    elif error.validator == "additionalProperties":
        fields = _additional_fields(error)

    if not fields:
        return [
            SchemaViolation(
                code=_violation_code(error.validator),
                field=_field_from_pointer(error_pointer),
                pointer=error_pointer,
                message=error.message,
            )
        ]
    return [
        SchemaViolation(
            code=_violation_code(error.validator),
            field=field,
            pointer=_child_pointer(error_pointer, field),
            message=error.message,
        )
        for field in fields
    ]


def validate_schema_value(
    value: Any,
    schema: SchemaDefinition,
    document: dict[str, Any],
    *,
    pointer: str = "",
    reference_stack: tuple[str, ...] = (),
) -> list[SchemaViolation]:
    """Validate a concrete value using the validator for the document's OAS version."""
    del reference_stack  # Kept for API compatibility with the former recursive validator.

    is_openapi_31 = str(document.get("openapi", "")).startswith("3.1.")
    validator_class = OAS31Validator if is_openapi_31 else OAS30Validator
    specification = DRAFT202012 if is_openapi_31 else DRAFT4
    prepared_document = copy.deepcopy(document)
    prepared_schema: Any = copy.deepcopy(schema)
    if is_openapi_31:
        prepared_document = _with_locatable_component_schemas(prepared_document)
        prepared_schema = _with_locatable_boolean_schemas(prepared_schema)
    prepared_document = _with_exact_floats(prepared_document)
    registry = Registry().with_resource(
        _DOCUMENT_URI,
        Resource(contents=prepared_document, specification=specification),
    )
    prepared_schema = _with_exact_floats(_with_absolute_local_refs(prepared_schema))
    prepared_value = _with_exact_floats(copy.deepcopy(value))
    runtime_variable_paths = _variable_paths(value)
    validator = validator_class(
        prepared_schema,
        format_checker=FORMAT_CHECKER,
        registry=registry,
    )

    violations: list[SchemaViolation] = []
    for error in validator.iter_errors(prepared_value):
        if _depends_only_on_runtime_variables(error, runtime_variable_paths):
            continue
        violations.extend(_error_violations(error, pointer))
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
