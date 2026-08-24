"""Load and normalize the supported OpenAPI 3.0/3.1 subset."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml
from openapi_spec_validator import validate
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError

from openapi_ai_test_evaluator.domain.openapi import (
    SUPPORTED_STRING_FORMATS,
    OpenAPISpec,
    OperationModel,
    ParameterLocation,
    ParameterModel,
    RequestBodyModel,
    ResponseModel,
    SchemaDefinition,
)

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
SUPPORTED_OPENAPI_31_DIALECTS = frozenset(
    {
        "https://spec.openapis.org/oas/3.1/dialect/base",
        "https://json-schema.org/draft/2020-12/schema",
    }
)
UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$dynamicAnchor",
        "$dynamicRef",
        "$recursiveAnchor",
        "$recursiveRef",
        "additionalItems",
        "contains",
        "dependentRequired",
        "dependentSchemas",
        "else",
        "if",
        "maxContains",
        "minContains",
        "not",
        "patternProperties",
        "prefixItems",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)


class SpecLoadError(ValueError):
    """An OpenAPI document could not be loaded or normalized."""


def resolve_local_ref(document: dict[str, Any], reference: str) -> Any:
    """Resolve one RFC 6901 local JSON reference."""
    if not reference.startswith("#/"):
        raise SpecLoadError(f"external references are not supported in V1: {reference}")

    current: Any = document
    for encoded_part in reference[2:].split("/"):
        part = encoded_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise SpecLoadError(f"unresolvable local reference: {reference}")
        current = current[part]
    return copy.deepcopy(current)


def resolve_reference_object(document: dict[str, Any], value: Any) -> Any:
    """Resolve a Reference Object, accepting only the annotations allowed in 3.1."""
    if not isinstance(value, dict) or "$ref" not in value:
        return value
    reference = value["$ref"]
    if not isinstance(reference, str):
        raise SpecLoadError("$ref must be a string")
    allowed_keys = {"$ref"}
    if str(document.get("openapi", "")).startswith("3.1."):
        allowed_keys.update({"summary", "description"})
    if unsupported_keys := set(value) - allowed_keys:
        keys = ", ".join(sorted(unsupported_keys))
        raise SpecLoadError(f"$ref objects cannot contain sibling keys in V1 ({keys}): {reference}")
    return resolve_local_ref(document, reference)


def _unsupported_schema_reasons(
    schema: SchemaDefinition,
    document: dict[str, Any],
    location: str,
    reference_stack: tuple[str, ...] = (),
) -> list[str]:
    if isinstance(schema, bool):
        return []

    reasons = [
        f"unsupported schema keyword {keyword!r} at {location}.{keyword}"
        for keyword in sorted(UNSUPPORTED_SCHEMA_KEYWORDS & schema.keys())
    ]
    format_name = schema.get("format")
    if isinstance(format_name, str) and format_name not in SUPPORTED_STRING_FORMATS:
        reasons.append(f"unsupported string format {format_name!r} at {location}.format")

    reference = schema.get("$ref")
    if isinstance(reference, str) and reference not in reference_stack:
        if reference.startswith("#/"):
            resolved = resolve_local_ref(document, reference)
            if isinstance(resolved, (dict, bool)):
                reasons.extend(
                    _unsupported_schema_reasons(
                        resolved,
                        document,
                        f"{location}.$ref({reference})",
                        (*reference_stack, reference),
                    )
                )
        else:
            reasons.append(f"external schema reference is unsupported at {location}.$ref")

    for keyword in (
        "items",
        "additionalProperties",
        "not",
        "if",
        "then",
        "else",
        "contains",
        "propertyNames",
        "unevaluatedProperties",
        "unevaluatedItems",
    ):
        child = schema.get(keyword)
        if isinstance(child, (dict, bool)):
            reasons.extend(
                _unsupported_schema_reasons(
                    child, document, f"{location}.{keyword}", reference_stack
                )
            )

    for keyword in ("properties", "patternProperties", "dependentSchemas", "$defs", "definitions"):
        children = schema.get(keyword)
        if not isinstance(children, dict):
            continue
        for name, child in children.items():
            if isinstance(child, (dict, bool)):
                reasons.extend(
                    _unsupported_schema_reasons(
                        child,
                        document,
                        f"{location}.{keyword}[{name!r}]",
                        reference_stack,
                    )
                )

    for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
        children = schema.get(keyword)
        if not isinstance(children, list):
            continue
        for index, child in enumerate(children):
            if isinstance(child, (dict, bool)):
                reasons.extend(
                    _unsupported_schema_reasons(
                        child,
                        document,
                        f"{location}.{keyword}[{index}]",
                        reference_stack,
                    )
                )

    return reasons


def _schema_from_content(content: Any) -> tuple[SchemaDefinition | None, list[str]]:
    if not isinstance(content, dict):
        return None, []
    json_media = content.get("application/json")
    if isinstance(json_media, dict) and isinstance(json_media.get("schema"), (dict, bool)):
        return copy.deepcopy(json_media["schema"]), []
    if content:
        return None, ["only application/json content is supported in V1"]
    return None, []


def _normalize_parameter(document: dict[str, Any], raw_parameter: Any) -> ParameterModel:
    parameter = resolve_reference_object(document, raw_parameter)
    if not isinstance(parameter, dict):
        raise SpecLoadError("OpenAPI parameters must be mappings")
    try:
        location = ParameterLocation(parameter["in"])
    except (KeyError, ValueError) as error:
        raise SpecLoadError(f"unsupported parameter location: {parameter.get('in')}") from error
    schema = parameter.get("schema", {})
    if not isinstance(schema, (dict, bool)):
        raise SpecLoadError(f"parameter {parameter.get('name')} has an invalid schema")
    return ParameterModel(
        name=str(parameter.get("name", "")),
        location=location,
        required=bool(parameter.get("required", False)),
        schema_definition=copy.deepcopy(schema),
        description=parameter.get("description"),
    )


def _merge_parameters(
    document: dict[str, Any],
    path_parameters: Any,
    operation_parameters: Any,
) -> list[ParameterModel]:
    merged: dict[tuple[ParameterLocation, str], ParameterModel] = {}
    for collection in (path_parameters or [], operation_parameters or []):
        if not isinstance(collection, list):
            raise SpecLoadError("OpenAPI parameters must be a list")
        for raw_parameter in collection:
            parameter = _normalize_parameter(document, raw_parameter)
            name_key = (
                parameter.name.casefold()
                if parameter.location is ParameterLocation.HEADER
                else parameter.name
            )
            merged[(parameter.location, name_key)] = parameter
    return list(merged.values())


def _normalize_request_body(
    document: dict[str, Any], raw_request_body: Any
) -> tuple[RequestBodyModel | None, list[str]]:
    if raw_request_body is None:
        return None, []
    request_body = resolve_reference_object(document, raw_request_body)
    if not isinstance(request_body, dict):
        raise SpecLoadError("requestBody must be a mapping")
    schema, unsupported = _schema_from_content(request_body.get("content"))
    return RequestBodyModel(
        required=bool(request_body.get("required", False)),
        schema_definition=schema,
        description=request_body.get("description"),
    ), unsupported


def _normalize_responses(
    document: dict[str, Any], raw_responses: Any
) -> tuple[dict[str, ResponseModel], list[str]]:
    if not isinstance(raw_responses, dict):
        raise SpecLoadError("responses must be a mapping")
    responses: dict[str, ResponseModel] = {}
    unsupported: list[str] = []
    for raw_status, raw_response in raw_responses.items():
        response = resolve_reference_object(document, raw_response)
        if not isinstance(response, dict):
            raise SpecLoadError(f"response {raw_status} must be a mapping")
        schema, response_unsupported = _schema_from_content(response.get("content"))
        unsupported.extend(response_unsupported)
        status = str(raw_status)
        responses[status] = ResponseModel(
            status_code=status,
            schema_definition=schema,
            description=response.get("description"),
        )
    return responses, unsupported


def _generated_operation_id(method: str, path: str) -> str:
    path_tokens = re.sub(r"\{([^}]+)\}", r"by_\1", path.strip("/"))
    normalized_path = re.sub(r"[^A-Za-z0-9]+", "_", path_tokens).strip("_")
    return f"{method}_{normalized_path or 'root'}"


def _derived_spec_id(title: str, version: str) -> str:
    value = f"{title}-{version}".lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def _normalize_document(document: dict[str, Any]) -> OpenAPISpec:
    openapi_version = str(document.get("openapi", ""))
    if not openapi_version.startswith(("3.0.", "3.1.")):
        raise SpecLoadError(
            f"V1 supports OpenAPI 3.0.x and 3.1.x, received {openapi_version or 'unknown'}"
        )
    schema_dialect = document.get("jsonSchemaDialect")
    if (
        openapi_version.startswith("3.1.")
        and schema_dialect is not None
        and schema_dialect not in SUPPORTED_OPENAPI_31_DIALECTS
    ):
        raise SpecLoadError(f"unsupported jsonSchemaDialect: {schema_dialect}")

    info = document.get("info")
    paths = document.get("paths")
    if not isinstance(info, dict) or not isinstance(paths, dict):
        raise SpecLoadError("OpenAPI documents require info and paths mappings")
    title = str(info.get("title", ""))
    version = str(info.get("version", ""))
    spec_id = str(document.get("x-oate-id") or _derived_spec_id(title, version))

    operations: dict[str, OperationModel] = {}
    for path, raw_path_item in paths.items():
        path_item = resolve_reference_object(document, raw_path_item)
        if not isinstance(path_item, dict):
            raise SpecLoadError(f"path item {path} must be a mapping")
        for method in HTTP_METHODS:
            raw_operation = path_item.get(method)
            if raw_operation is None:
                continue
            if not isinstance(raw_operation, dict):
                raise SpecLoadError(f"operation {method.upper()} {path} must be a mapping")
            operation_id = str(
                raw_operation.get("operationId") or _generated_operation_id(method, str(path))
            )
            if operation_id in operations:
                raise SpecLoadError(f"duplicate operationId: {operation_id}")

            parameters = _merge_parameters(
                document,
                path_item.get("parameters"),
                raw_operation.get("parameters"),
            )
            request_body, request_unsupported = _normalize_request_body(
                document, raw_operation.get("requestBody")
            )
            responses, response_unsupported = _normalize_responses(
                document, raw_operation.get("responses")
            )
            schema_unsupported: list[str] = []
            for parameter in parameters:
                schema_unsupported.extend(
                    _unsupported_schema_reasons(
                        parameter.schema_definition,
                        document,
                        f"parameter {parameter.location.value}.{parameter.name} schema",
                    )
                )
            if request_body is not None and request_body.schema_definition is not None:
                schema_unsupported.extend(
                    _unsupported_schema_reasons(
                        request_body.schema_definition,
                        document,
                        "request body schema",
                    )
                )
            for status, response in responses.items():
                if response.schema_definition is not None:
                    schema_unsupported.extend(
                        _unsupported_schema_reasons(
                            response.schema_definition,
                            document,
                            f"response {status} schema",
                        )
                    )
            unsupported_reasons = list(
                dict.fromkeys([*request_unsupported, *response_unsupported, *schema_unsupported])
            )
            operations[operation_id] = OperationModel(
                operation_id=operation_id,
                method=method.upper(),
                path=str(path),
                parameters=parameters,
                request_body=request_body,
                responses=responses,
                unsupported_reasons=unsupported_reasons,
                summary=raw_operation.get("summary"),
                description=raw_operation.get("description"),
            )

    return OpenAPISpec(
        spec_id=spec_id,
        openapi_version=openapi_version,
        title=title,
        version=version,
        operations=operations,
        document=document,
        description=info.get("description"),
    )


def load_openapi(path: Path) -> OpenAPISpec:
    """Load, validate, and normalize a supported OpenAPI document."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SpecLoadError(f"cannot read {path}: {error}") from error
    except yaml.YAMLError as error:
        raise SpecLoadError(f"invalid YAML in {path}: {error}") from error

    if not isinstance(document, dict):
        raise SpecLoadError(f"{path} must contain a YAML mapping at the top level")
    try:
        validate(document)
    except OpenAPIValidationError as error:
        raise SpecLoadError(f"invalid OpenAPI document {path}: {error}") from error

    return _normalize_document(document)
