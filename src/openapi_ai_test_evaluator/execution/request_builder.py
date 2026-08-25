"""Build deterministic HTTP request inputs from validated TestPlan steps."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote

from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec, OperationModel
from openapi_ai_test_evaluator.domain.test_case import ExecutionConfig, RequestStep

_PATH_PARAMETER = re.compile(r"\{([^{}]+)\}")


class RequestBuildError(ValueError):
    """A validated step could not be resolved into an HTTP request."""

    def __init__(self, location: str, message: str) -> None:
        self.location = location
        self.message = message
        super().__init__(f"{location}: {message}")


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    """Transport-independent inputs for one JSON HTTP request."""

    operation_id: str
    method: str
    path: str
    path_parameters: tuple[tuple[str, str], ...]
    query: tuple[tuple[str, str], ...]
    headers: Mapping[str, str]
    has_json_body: bool
    json_body: JsonValue | None
    timeout_ms: int


def build_request(
    step: RequestStep,
    spec: OpenAPISpec,
    variables: Mapping[str, JsonValue],
    defaults: ExecutionConfig,
) -> PreparedRequest:
    """Resolve one TestPlan step without performing network I/O."""
    operation = spec.operations.get(step.operation_id)
    if operation is None:
        raise RequestBuildError("operation_id", f"unknown OpenAPI operation {step.operation_id!r}")

    path_values = {
        name: _resolve_runtime_value(value, variables, f"request.path.{name}")
        for name, value in step.request.path.items()
    }
    path, path_parameters = _render_path(operation, path_values)

    query = tuple(
        (
            parameter.name,
            _serialize_parameter(
                _resolve_runtime_value(
                    parameter.value,
                    variables,
                    f"request.query.{parameter.name}",
                ),
                f"request.query.{parameter.name}",
            ),
        )
        for parameter in step.request.query
    )

    headers = _merge_headers(
        defaults.headers,
        {
            name: _serialize_parameter(
                _resolve_runtime_value(value, variables, f"request.headers.{name}"),
                f"request.headers.{name}",
            )
            for name, value in step.request.headers.items()
        },
    )
    body = (
        _resolve_runtime_value(step.request.body, variables, "request.body")
        if step.request.body_present
        else None
    )

    return PreparedRequest(
        operation_id=operation.operation_id,
        method=operation.method.upper(),
        path=path,
        path_parameters=path_parameters,
        query=query,
        headers=headers,
        has_json_body=step.request.body_present,
        json_body=body,
        timeout_ms=defaults.timeout_ms,
    )


def encode_json_body(request: PreparedRequest) -> bytes | None:
    """Encode an explicitly present JSON body while preserving JSON null."""
    if not request.has_json_body:
        return None
    return json.dumps(
        request.json_body,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _resolve_runtime_value(
    value: JsonValue,
    variables: Mapping[str, JsonValue],
    location: str,
) -> JsonValue:
    if isinstance(value, dict):
        if set(value) == {"$var"}:
            variable_name = value["$var"]
            if not isinstance(variable_name, str) or variable_name not in variables:
                raise RequestBuildError(location, f"unknown runtime variable {variable_name!r}")
            return variables[variable_name]
        return {
            key: _resolve_runtime_value(nested, variables, f"{location}.{key}")
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_runtime_value(nested, variables, f"{location}[{index}]")
            for index, nested in enumerate(value)
        ]
    return value


def _render_path(
    operation: OperationModel,
    values: Mapping[str, JsonValue],
) -> tuple[str, tuple[tuple[str, str], ...]]:
    path = operation.path
    declared_names = tuple(dict.fromkeys(_PATH_PARAMETER.findall(path)))
    declared_name_set = set(declared_names)
    if missing := declared_name_set - values.keys():
        missing_list = ", ".join(sorted(missing))
        raise RequestBuildError("request.path", f"missing path parameters: {missing_list}")
    if extra := values.keys() - declared_name_set:
        extra_list = ", ".join(sorted(extra))
        raise RequestBuildError("request.path", f"unknown path parameters: {extra_list}")

    serialized_parameters: list[tuple[str, str]] = []
    for name in declared_names:
        serialized = _serialize_parameter(values[name], f"request.path.{name}")
        serialized_parameters.append((name, serialized))
        path = path.replace(f"{{{name}}}", quote(serialized, safe=""))
    return path, tuple(serialized_parameters)


def _serialize_parameter(value: JsonValue, location: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise RequestBuildError(
        location,
        "array and object parameter serialization is outside the V1 runtime subset",
    )


def _merge_headers(defaults: Mapping[str, str], step_headers: Mapping[str, str]) -> dict[str, str]:
    merged: dict[str, tuple[str, str]] = {
        name.casefold(): (name, value) for name, value in defaults.items()
    }
    for name, value in step_headers.items():
        merged[name.casefold()] = (name, value)
    return {name: value for name, value in merged.values()}
