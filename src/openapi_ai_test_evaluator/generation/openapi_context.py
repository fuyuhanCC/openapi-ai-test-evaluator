"""Build deterministic, provider-independent context from normalized OpenAPI data."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec, SchemaDefinition
from openapi_ai_test_evaluator.spec.loader import resolve_local_ref


def build_openapi_context(spec: OpenAPISpec) -> dict[str, Any]:
    """Return only the OpenAPI information needed to generate test cases."""
    operation_contexts: list[dict[str, Any]] = []
    root_schemas: list[SchemaDefinition] = []

    for operation in sorted(spec.operations.values(), key=lambda item: item.operation_id):
        parameters: list[dict[str, Any]] = []
        for parameter in sorted(
            operation.parameters,
            key=lambda item: (item.location.value, item.name.casefold()),
        ):
            schema = _compact_schema(parameter.schema_definition)
            root_schemas.append(schema)
            parameters.append(
                {
                    "name": parameter.name,
                    "location": parameter.location.value,
                    "required": parameter.required,
                    "schema": schema,
                }
            )
            if parameter.description is not None:
                parameters[-1]["description"] = parameter.description

        operation_context: dict[str, Any] = {
            "operation_id": operation.operation_id,
            "method": operation.method,
            "path": operation.path,
            "parameters": parameters,
            "responses": {},
            "supported": not operation.unsupported_reasons,
        }
        if operation.summary is not None:
            operation_context["summary"] = operation.summary
        if operation.description is not None:
            operation_context["description"] = operation.description

        if operation.request_body is not None:
            request_schema = _optional_compact_schema(operation.request_body.schema_definition)
            if request_schema is not None:
                root_schemas.append(request_schema)
            operation_context["request_body"] = {
                "required": operation.request_body.required,
                "schema": request_schema,
            }
            if operation.request_body.description is not None:
                operation_context["request_body"]["description"] = (
                    operation.request_body.description
                )

        responses: dict[str, dict[str, Any]] = {}
        for status, response in sorted(operation.responses.items()):
            response_schema = _optional_compact_schema(response.schema_definition)
            if response_schema is not None:
                root_schemas.append(response_schema)
            responses[status] = {"schema": response_schema}
            if response.description is not None:
                responses[status]["description"] = response.description
        operation_context["responses"] = responses

        if operation.unsupported_reasons:
            operation_context["unsupported_reasons"] = list(operation.unsupported_reasons)

        operation_contexts.append(operation_context)

    context = {
        "spec_id": spec.spec_id,
        "openapi_version": spec.openapi_version,
        "title": spec.title,
        "api_version": spec.version,
        "operations": operation_contexts,
        "referenced_schemas": _collect_referenced_schemas(root_schemas, spec.document),
    }
    if spec.description is not None:
        context["description"] = spec.description
    return context


def _optional_compact_schema(schema: SchemaDefinition | None) -> SchemaDefinition | None:
    if schema is None:
        return None
    return _compact_schema(schema)


def _compact_schema(schema: SchemaDefinition) -> SchemaDefinition:
    if isinstance(schema, bool):
        return schema
    return _remove_vendor_extensions(copy.deepcopy(schema))


def _remove_vendor_extensions(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _remove_vendor_extensions(child)
            for key, child in value.items()
            if not str(key).lower().startswith("x-")
        }
    if isinstance(value, list):
        return [_remove_vendor_extensions(child) for child in value]
    return value


def _collect_referenced_schemas(
    root_schemas: Iterable[SchemaDefinition],
    document: dict[str, Any],
) -> dict[str, SchemaDefinition]:
    pending = sorted(_find_local_references(root_schemas))
    collected: dict[str, SchemaDefinition] = {}

    while pending:
        reference = pending.pop(0)
        if reference in collected:
            continue

        resolved = resolve_local_ref(document, reference)
        if not isinstance(resolved, (dict, bool)):
            continue
        compacted = _compact_schema(resolved)
        collected[reference] = compacted

        discovered = _find_local_references([compacted]) - collected.keys()
        pending = sorted(set(pending) | discovered)

    return {reference: collected[reference] for reference in sorted(collected)}


def _find_local_references(values: Iterable[Any]) -> set[str]:
    references: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/"):
                references.add(reference)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for value in values:
        visit(value)
    return references
