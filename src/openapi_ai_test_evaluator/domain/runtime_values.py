"""Validation shared by case values and execution-time variables."""

from pydantic import JsonValue


def validate_runtime_value(value: JsonValue) -> JsonValue:
    """Validate reserved variable references inside otherwise ordinary JSON."""
    if isinstance(value, dict):
        if "$var" in value:
            if set(value) != {"$var"}:
                raise ValueError("a $var reference cannot contain sibling keys")
            variable_name = value["$var"]
            if not isinstance(variable_name, str) or not variable_name.strip():
                raise ValueError("$var must contain a non-empty variable name")
            return value
        for nested in value.values():
            validate_runtime_value(nested)
    elif isinstance(value, list):
        for nested in value:
            validate_runtime_value(nested)
    return value
