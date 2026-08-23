"""Semantic validation between API test cases and normalized OpenAPI documents."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from openapi_ai_test_evaluator.domain.contracts import ContractModel
from openapi_ai_test_evaluator.domain.openapi import (
    OpenAPISpec,
    OperationModel,
    ParameterLocation,
    SchemaDefinition,
)
from openapi_ai_test_evaluator.domain.test_case import (
    AssertionOperator,
    ExecutionConfig,
    RelationType,
    RequestMode,
    RequestStep,
    ScenarioRelation,
    TestCase,
    TestCaseBatch,
    ViolationCode,
)
from openapi_ai_test_evaluator.domain.test_plan import TestPlan
from openapi_ai_test_evaluator.validation.schema_values import (
    is_variable_reference,
    schema_at_pointer,
    validate_schema_value,
    variable_references,
)


class SemanticIssue(ContractModel):
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class _DetectedViolation:
    code: ViolationCode
    location: str
    field: str
    message: str

    @property
    def key(self) -> tuple[str, str, str]:
        return self.code.value, self.location, self.field


def _issue(code: str, path: str, message: str) -> SemanticIssue:
    return SemanticIssue(code=code, path=path, message=message)


def _step_variable_references(step: RequestStep) -> set[str]:
    request = step.request
    references = variable_references(request.body)
    for value in request.path.values():
        references.update(variable_references(value))
    for parameter in request.query:
        references.update(variable_references(parameter.value))
    for value in request.headers.values():
        references.update(variable_references(value))
    for assertion in step.assertions:
        references.update(variable_references(assertion.expected))
    return references


def _statically_resolved_parameter_value(
    value: Any,
    plan_variables: dict[str, Any],
) -> Any:
    if not is_variable_reference(value):
        return value
    variable_name = value["$var"]
    return plan_variables.get(variable_name)


def _validate_parameter_collection(
    step: RequestStep,
    operation: OperationModel,
    spec: OpenAPISpec,
    path: str,
    default_headers: dict[str, str],
    plan_variables: dict[str, Any],
) -> tuple[list[SemanticIssue], list[_DetectedViolation]]:
    issues: list[SemanticIssue] = []
    detected: list[_DetectedViolation] = []

    provided: dict[ParameterLocation, list[tuple[str, Any]]] = {
        ParameterLocation.PATH: list(step.request.path.items()),
        ParameterLocation.QUERY: [
            (parameter.name, parameter.value) for parameter in step.request.query
        ],
        ParameterLocation.HEADER: [
            *default_headers.items(),
            *step.request.headers.items(),
        ],
    }

    for location, values in provided.items():
        for name, value in values:
            parameter = operation.parameter(location, name)
            parameter_path = f"{path}.request.{location.value}.{name}"
            # OpenAPI parameter objects describe application-level headers.
            # Plans may also provide normal HTTP transport headers such as
            # Accept, Content-Type, Authorization, or tracing headers.
            if parameter is None and location is not ParameterLocation.HEADER:
                issues.append(
                    _issue(
                        "unknown_parameter",
                        parameter_path,
                        f"{operation.operation_id} has no {location.value} parameter {name!r}",
                    )
                )
                continue

            resolved_value = _statically_resolved_parameter_value(value, plan_variables)
            if isinstance(resolved_value, (dict, list)):
                issues.append(
                    _issue(
                        "unsupported_parameter_serialization",
                        parameter_path,
                        (
                            f"{location.value} parameter {name!r} uses an array or object; "
                            "V1 supports only scalar HTTP parameter values"
                        ),
                    )
                )
                continue

            if parameter is None:
                continue
            for violation in validate_schema_value(
                value,
                parameter.schema_definition,
                spec.document,
                pointer=f"/{name}",
            ):
                detected.append(
                    _DetectedViolation(
                        code=violation.code,
                        location=location.value,
                        field=name,
                        message=violation.message,
                    )
                )

    for parameter in operation.parameters:
        if not parameter.required:
            continue
        supplied_names = [name for name, _ in provided[parameter.location]]
        supplied = (
            any(name.casefold() == parameter.name.casefold() for name in supplied_names)
            if parameter.location is ParameterLocation.HEADER
            else parameter.name in supplied_names
        )
        if not supplied:
            detected.append(
                _DetectedViolation(
                    code=ViolationCode.MISSING_REQUIRED,
                    location=parameter.location.value,
                    field=parameter.name,
                    message=f"required {parameter.location.value} parameter is missing",
                )
            )

    return issues, detected


def _validate_request_body(
    step: RequestStep,
    operation: OperationModel,
    spec: OpenAPISpec,
    path: str,
) -> tuple[list[SemanticIssue], list[_DetectedViolation]]:
    issues: list[SemanticIssue] = []
    detected: list[_DetectedViolation] = []
    body = step.request.body
    request_body = operation.request_body

    if request_body is None:
        if body is not None:
            issues.append(
                _issue(
                    "unexpected_request_body",
                    f"{path}.request.body",
                    f"{operation.operation_id} does not declare a request body",
                )
            )
        return issues, detected

    if body is None:
        if request_body.required:
            detected.append(
                _DetectedViolation(
                    code=ViolationCode.MISSING_REQUIRED,
                    location="body",
                    field="$body",
                    message="required request body is missing",
                )
            )
        return issues, detected

    if request_body.schema_definition is None:
        issues.append(
            _issue(
                "unsupported_request_media_type",
                f"{path}.request.body",
                f"{operation.operation_id} has no supported application/json request schema",
            )
        )
        return issues, detected

    for violation in validate_schema_value(
        body,
        request_body.schema_definition,
        spec.document,
    ):
        detected.append(
            _DetectedViolation(
                code=violation.code,
                location="body",
                field=violation.field,
                message=violation.message,
            )
        )
    return issues, detected


def _compare_declared_violations(
    step: RequestStep,
    detected: list[_DetectedViolation],
    path: str,
) -> list[SemanticIssue]:
    issues: list[SemanticIssue] = []
    if step.request.mode is RequestMode.CONFORMANT:
        for violation in detected:
            issues.append(
                _issue(
                    "request_schema_violation",
                    f"{path}.request",
                    f"{violation.location}.{violation.field}: {violation.message}",
                )
            )
        return issues

    actual_by_key = {violation.key: violation for violation in detected}
    expected_keys = {
        (violation.code.value, violation.location, violation.field)
        for violation in step.request.expected_violations
    }
    actual_keys = set(actual_by_key)

    for code, location, field in sorted(expected_keys - actual_keys):
        issues.append(
            _issue(
                "false_expected_violation",
                f"{path}.request.expected_violations",
                f"declared violation {code} at {location}.{field} does not occur",
            )
        )
    for key in sorted(actual_keys - expected_keys):
        violation = actual_by_key[key]
        issues.append(
            _issue(
                "undeclared_request_violation",
                f"{path}.request",
                (
                    f"{violation.code.value} at "
                    f"{violation.location}.{violation.field} was not declared"
                ),
            )
        )
    return issues


def _response_for_status(operation: OperationModel, status: int) -> Any:
    return operation.responses.get(str(status)) or operation.responses.get("default")


def _expected_statuses(step: RequestStep) -> list[int]:
    return [
        assertion.expected
        for assertion in step.assertions
        if assertion.operator is AssertionOperator.STATUS_IS
        and isinstance(assertion.expected, int)
        and not isinstance(assertion.expected, bool)
    ]


def _candidate_response_schemas(
    step: RequestStep, operation: OperationModel
) -> list[SchemaDefinition]:
    statuses = _expected_statuses(step)
    if not statuses:
        statuses = [
            int(status)
            for status in operation.responses
            if status.isdigit() and 200 <= int(status) < 300
        ]
    schemas: list[SchemaDefinition] = []
    for status in statuses:
        response = _response_for_status(operation, status)
        if response is not None and response.schema_definition is not None:
            schemas.append(response.schema_definition)
    return schemas


def _pointer_exists_in_any_schema(
    schemas: list[SchemaDefinition], pointer: str, spec: OpenAPISpec
) -> bool:
    return any(schema_at_pointer(schema, pointer, spec.document) is not None for schema in schemas)


def _validate_response_contract(
    step: RequestStep,
    operation: OperationModel,
    spec: OpenAPISpec,
    path: str,
) -> list[SemanticIssue]:
    issues: list[SemanticIssue] = []
    expected_statuses = _expected_statuses(step)

    for status in expected_statuses:
        if _response_for_status(operation, status) is None:
            issues.append(
                _issue(
                    "undeclared_response_status",
                    f"{path}.assertions",
                    f"{operation.operation_id} does not declare response status {status}",
                )
            )

    schemas = _candidate_response_schemas(step, operation)
    for assertion_index, assertion in enumerate(step.assertions):
        assertion_path = f"{path}.assertions[{assertion_index}]"
        if assertion.operator is AssertionOperator.SCHEMA_MATCHES and not schemas:
            issues.append(
                _issue(
                    "missing_response_schema",
                    assertion_path,
                    f"{operation.operation_id} has no JSON schema for the expected response",
                )
            )
        if assertion.actual is not None and assertion.actual.source == "response.body":
            pointer = assertion.actual.pointer or ""
            if not _pointer_exists_in_any_schema(schemas, pointer, spec):
                issues.append(
                    _issue(
                        "unknown_response_pointer",
                        f"{assertion_path}.actual.pointer",
                        f"response pointer {pointer!r} is absent from the expected response schema",
                    )
                )

    for extraction_index, extraction in enumerate(step.extract):
        if extraction.source != "response.body":
            continue
        if not _pointer_exists_in_any_schema(schemas, extraction.pointer, spec):
            issues.append(
                _issue(
                    "unknown_response_pointer",
                    f"{path}.extract[{extraction_index}].pointer",
                    f"response pointer {extraction.pointer!r} is absent from the response schema",
                )
            )
    return issues


def _validate_step(
    step: RequestStep,
    spec: OpenAPISpec,
    path: str,
    available_variables: set[str],
    default_headers: dict[str, str],
    plan_variables: dict[str, Any],
) -> tuple[list[SemanticIssue], OperationModel | None]:
    issues: list[SemanticIssue] = []
    missing_variables = _step_variable_references(step) - available_variables
    for variable in sorted(missing_variables):
        issues.append(
            _issue(
                "unknown_variable",
                f"{path}.request",
                f"variable {variable!r} is not defined before this step",
            )
        )

    operation = spec.operations.get(step.operation_id)
    if operation is None:
        issues.append(
            _issue(
                "unknown_operation",
                f"{path}.operation_id",
                f"operationId {step.operation_id!r} does not exist in {spec.spec_id}",
            )
        )
        return issues, None

    for reason in operation.unsupported_reasons:
        issues.append(_issue("unsupported_operation", path, f"{operation.operation_id}: {reason}"))

    parameter_issues, parameter_violations = _validate_parameter_collection(
        step,
        operation,
        spec,
        path,
        default_headers,
        plan_variables,
    )
    body_issues, body_violations = _validate_request_body(step, operation, spec, path)
    issues.extend(parameter_issues)
    issues.extend(body_issues)
    issues.extend(
        _compare_declared_violations(
            step,
            [*parameter_violations, *body_violations],
            path,
        )
    )
    issues.extend(_validate_response_contract(step, operation, spec, path))
    return issues, operation


def _schema_for_relation_location(
    step: RequestStep,
    operation: OperationModel,
    location: str,
) -> list[SchemaDefinition]:
    if location == "request.body":
        if operation.request_body is None or operation.request_body.schema_definition is None:
            return []
        return [operation.request_body.schema_definition]
    if location == "response.body":
        return _candidate_response_schemas(step, operation)
    return []


def _query_signature(step: RequestStep) -> list[tuple[str, str]]:
    """Return an order-preserving, hashable representation of query parameters."""
    return [
        (
            parameter.name,
            json.dumps(
                parameter.value,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        for parameter in step.request.query
    ]


def _path_variable_references(step: RequestStep) -> set[str]:
    references: set[str] = set()
    for value in step.request.path.values():
        references.update(variable_references(value))
    return references


def _paths_share_resource(source: RequestStep, follow_up: RequestStep) -> bool:
    if _path_variable_references(source) & _path_variable_references(follow_up):
        return True
    for name, source_value in source.request.path.items():
        if name not in follow_up.request.path:
            continue
        if json.dumps(source_value, sort_keys=True) == json.dumps(
            follow_up.request.path[name], sort_keys=True
        ):
            return True
    return False


def _schemas_at_pointer(
    schemas: list[SchemaDefinition], pointer: str, spec: OpenAPISpec
) -> list[SchemaDefinition]:
    found: list[SchemaDefinition] = []
    for schema in schemas:
        pointed_schema = schema_at_pointer(schema, pointer, spec.document)
        if pointed_schema is not None:
            found.append(pointed_schema)
    return found


def _schema_types_compatible(
    source_schemas: list[SchemaDefinition], follow_schemas: list[SchemaDefinition]
) -> bool:
    for source_schema in source_schemas:
        for follow_schema in follow_schemas:
            if isinstance(source_schema, bool) or isinstance(follow_schema, bool):
                return True
            source_types = _schema_type_names(source_schema)
            follow_types = _schema_type_names(follow_schema)
            if source_types is None or follow_types is None:
                return True
            if source_types & follow_types:
                return True
            if (
                "integer" in source_types
                and "number" in follow_types
                or "number" in source_types
                and "integer" in follow_types
            ):
                return True
    return False


def _schema_type_names(schema: dict[str, Any]) -> set[str] | None:
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return {schema_type}
    if isinstance(schema_type, list):
        return {item for item in schema_type if isinstance(item, str)}
    return None


def _validate_scenario_relation(
    relation: ScenarioRelation,
    step_models: dict[str, tuple[RequestStep, OperationModel]],
    step_positions: dict[str, int],
    spec: OpenAPISpec,
    path: str,
) -> list[SemanticIssue]:
    issues: list[SemanticIssue] = []
    source = step_models.get(relation.source_step)
    follow_up = step_models.get(relation.follow_up_step)
    if source is None or follow_up is None:
        return issues
    source_step, source_operation = source
    follow_step, follow_operation = follow_up
    source_schemas = _candidate_response_schemas(source_step, source_operation)
    follow_schemas = _candidate_response_schemas(follow_step, follow_operation)

    source_position = step_positions[relation.source_step]
    follow_position = step_positions[relation.follow_up_step]
    if source_position >= follow_position:
        issues.append(
            _issue(
                "relation_step_order_invalid",
                path,
                "source step must execute before the follow-up step",
            )
        )

    expected_methods: dict[RelationType, tuple[set[str], set[str]]] = {
        RelationType.REPEATED_READ: ({"GET"}, {"GET"}),
        RelationType.QUERY_ORDER: ({"GET"}, {"GET"}),
        RelationType.PAGINATION: ({"GET"}, {"GET"}),
        RelationType.CREATE_READ: ({"POST", "PUT"}, {"GET"}),
        RelationType.UPDATE_READ: ({"PUT", "PATCH"}, {"GET"}),
        RelationType.DELETE_READ: ({"DELETE"}, {"GET"}),
    }
    source_methods, follow_methods = expected_methods[relation.type]
    if (
        source_operation.method not in source_methods
        or follow_operation.method not in follow_methods
    ):
        issues.append(
            _issue(
                "relation_method_mismatch",
                path,
                (
                    f"{relation.type.value} received "
                    f"{source_operation.method} -> {follow_operation.method}"
                ),
            )
        )

    if relation.type in {
        RelationType.REPEATED_READ,
        RelationType.QUERY_ORDER,
        RelationType.PAGINATION,
    }:
        if source_operation.operation_id != follow_operation.operation_id:
            issues.append(
                _issue(
                    "relation_operation_mismatch",
                    path,
                    f"{relation.type.value} requires source and follow-up to use one operation",
                )
            )

    if relation.type is RelationType.REPEATED_READ:
        if source_step.request != follow_step.request:
            issues.append(
                _issue(
                    "relation_request_mismatch",
                    path,
                    "repeated-read relation requires equivalent requests",
                )
            )
        intervening_mutations = [
            step_id
            for step_id, position in step_positions.items()
            if source_position < position < follow_position
            and step_models[step_id][1].method not in {"GET", "HEAD", "OPTIONS"}
        ]
        if intervening_mutations:
            issues.append(
                _issue(
                    "relation_intervening_mutation",
                    path,
                    (
                        "repeated-read relation contains mutating steps: "
                        + ", ".join(intervening_mutations)
                    ),
                )
            )

    if relation.type is RelationType.QUERY_ORDER:
        source_query = _query_signature(source_step)
        follow_query = _query_signature(follow_step)
        if Counter(source_query) != Counter(follow_query):
            issues.append(
                _issue(
                    "relation_query_mismatch",
                    path,
                    "query-order relation requires the same parameter names and values",
                )
            )
        elif source_query == follow_query:
            issues.append(
                _issue(
                    "relation_query_order_unchanged",
                    path,
                    "query-order relation requires a different parameter order",
                )
            )

    if relation.type is RelationType.PAGINATION:
        size_parameter = relation.page_size_parameter or ""
        source_size_values = [
            parameter.value
            for parameter in source_step.request.query
            if parameter.name == size_parameter
        ]
        follow_size_values = [
            parameter.value
            for parameter in follow_step.request.query
            if parameter.name == size_parameter
        ]
        source_context = Counter(
            item for item in _query_signature(source_step) if item[0] != size_parameter
        )
        follow_context = Counter(
            item for item in _query_signature(follow_step) if item[0] != size_parameter
        )
        if source_context != follow_context:
            issues.append(
                _issue(
                    "relation_pagination_context_mismatch",
                    path,
                    "pagination relation requires all non-size query parameters to match",
                )
            )
        sizes_are_comparable = (
            len(source_size_values) == 1
            and len(follow_size_values) == 1
            and isinstance(source_size_values[0], (int, float))
            and not isinstance(source_size_values[0], bool)
            and isinstance(follow_size_values[0], (int, float))
            and not isinstance(follow_size_values[0], bool)
        )
        if not sizes_are_comparable or follow_size_values[0] <= source_size_values[0]:
            issues.append(
                _issue(
                    "relation_pagination_size_invalid",
                    path,
                    (
                        f"follow-up {size_parameter!r} must be a concrete number "
                        "larger than the source value"
                    ),
                )
            )
    if relation.type is RelationType.REPEATED_READ:
        for pointer in relation.compare_pointers:
            if not _pointer_exists_in_any_schema(source_schemas, pointer, spec):
                issues.append(
                    _issue("unknown_relation_pointer", path, f"source response lacks {pointer!r}")
                )
            if not _pointer_exists_in_any_schema(follow_schemas, pointer, spec):
                issues.append(
                    _issue(
                        "unknown_relation_pointer",
                        path,
                        f"follow-up response lacks {pointer!r}",
                    )
                )

    if relation.type in {RelationType.QUERY_ORDER, RelationType.PAGINATION}:
        collection = relation.collection_pointer or ""
        item_key = relation.item_key_pointer or ""
        item_pointer = f"{collection}/0{item_key}"
        for label, schemas in (("source", source_schemas), ("follow-up", follow_schemas)):
            if not _pointer_exists_in_any_schema(schemas, collection, spec):
                issues.append(
                    _issue(
                        "unknown_relation_pointer",
                        path,
                        f"{label} response lacks collection {collection!r}",
                    )
                )
            elif not _pointer_exists_in_any_schema(schemas, item_pointer, spec):
                issues.append(
                    _issue(
                        "unknown_relation_pointer",
                        path,
                        f"{label} collection items lack key {item_key!r}",
                    )
                )

    if relation.type in {RelationType.CREATE_READ, RelationType.UPDATE_READ}:
        for pair in relation.field_pairs:
            source_pair_schemas = _schema_for_relation_location(
                source_step, source_operation, pair.source.location
            )
            follow_pair_schemas = _schema_for_relation_location(
                follow_step, follow_operation, pair.follow_up.location
            )
            if pair.source.pointer is not None and not _pointer_exists_in_any_schema(
                source_pair_schemas, pair.source.pointer, spec
            ):
                issues.append(
                    _issue(
                        "unknown_relation_pointer",
                        path,
                        f"source {pair.source.location} lacks {pair.source.pointer!r}",
                    )
                )
            if pair.follow_up.pointer is not None and not _pointer_exists_in_any_schema(
                follow_pair_schemas, pair.follow_up.pointer, spec
            ):
                issues.append(
                    _issue(
                        "unknown_relation_pointer",
                        path,
                        f"follow-up {pair.follow_up.location} lacks {pair.follow_up.pointer!r}",
                    )
                )
            if pair.source.pointer is not None and pair.follow_up.pointer is not None:
                source_field_schemas = _schemas_at_pointer(
                    source_pair_schemas, pair.source.pointer, spec
                )
                follow_field_schemas = _schemas_at_pointer(
                    follow_pair_schemas, pair.follow_up.pointer, spec
                )
                if (
                    source_field_schemas
                    and follow_field_schemas
                    and not _schema_types_compatible(
                        source_field_schemas,
                        follow_field_schemas,
                    )
                ):
                    issues.append(
                        _issue(
                            "relation_field_type_mismatch",
                            path,
                            (
                                f"{pair.source.pointer!r} and "
                                f"{pair.follow_up.pointer!r} have incompatible schemas"
                            ),
                        )
                    )

    if relation.type is RelationType.CREATE_READ:
        extracted_variables = {extraction.variable for extraction in source_step.extract}
        if not extracted_variables & _path_variable_references(follow_step):
            issues.append(
                _issue(
                    "relation_resource_not_linked",
                    path,
                    "create-read follow-up path must use a variable extracted by the source",
                )
            )

    if relation.type in {RelationType.UPDATE_READ, RelationType.DELETE_READ}:
        if not _paths_share_resource(source_step, follow_step):
            issues.append(
                _issue(
                    "relation_resource_not_linked",
                    path,
                    "source and follow-up paths do not identify the same resource",
                )
            )

    if relation.type is RelationType.UPDATE_READ:
        baseline = (
            step_models.get(relation.baseline_step) if relation.baseline_step is not None else None
        )
        baseline_schemas: list[dict[str, Any]] = []
        if baseline is not None:
            baseline_step, baseline_operation = baseline
            baseline_schemas = _candidate_response_schemas(baseline_step, baseline_operation)
            if step_positions[relation.baseline_step] >= source_position:
                issues.append(
                    _issue(
                        "relation_baseline_order_invalid",
                        path,
                        "update baseline step must execute before the source update",
                    )
                )
        for pointer in relation.stable_follow_up_pointers:
            baseline_field_schemas = _schemas_at_pointer(baseline_schemas, pointer, spec)
            follow_field_schemas = _schemas_at_pointer(follow_schemas, pointer, spec)
            if not _pointer_exists_in_any_schema(baseline_schemas, pointer, spec):
                issues.append(
                    _issue(
                        "unknown_relation_pointer",
                        path,
                        f"baseline response lacks stable field {pointer!r}",
                    )
                )
            if not _pointer_exists_in_any_schema(follow_schemas, pointer, spec):
                issues.append(
                    _issue(
                        "unknown_relation_pointer",
                        path,
                        f"follow-up response lacks stable field {pointer!r}",
                    )
                )
            if (
                baseline_field_schemas
                and follow_field_schemas
                and not _schema_types_compatible(
                    baseline_field_schemas,
                    follow_field_schemas,
                )
            ):
                issues.append(
                    _issue(
                        "relation_field_type_mismatch",
                        path,
                        f"baseline and follow-up {pointer!r} have incompatible schemas",
                    )
                )

    if relation.type is RelationType.DELETE_READ:
        if not any(200 <= status < 300 for status in _expected_statuses(source_step)):
            issues.append(
                _issue(
                    "relation_delete_success_unasserted",
                    path,
                    "delete source step must assert a successful 2xx status",
                )
            )
        for status in relation.accepted_follow_up_statuses:
            if _response_for_status(follow_operation, status) is None:
                issues.append(
                    _issue(
                        "undeclared_relation_status",
                        path,
                        f"{follow_operation.operation_id} does not declare status {status}",
                    )
                )
    return issues


def _validate_scenario(
    scenario: TestCase,
    scenario_index: int,
    spec: OpenAPISpec,
    *,
    collection_path: str,
    default_headers: dict[str, str],
    initial_variables: dict[str, Any],
) -> list[SemanticIssue]:
    issues: list[SemanticIssue] = []
    available_variables = set(initial_variables)
    step_models: dict[str, tuple[RequestStep, OperationModel]] = {}
    step_positions: dict[str, int] = {}

    sections = (
        ("setup", scenario.setup),
        ("steps", scenario.steps),
        ("cleanup", scenario.cleanup),
    )
    for section_name, steps in sections:
        for step_index, step in enumerate(steps):
            path = f"{collection_path}[{scenario_index}].{section_name}[{step_index}]"
            step_issues, operation = _validate_step(
                step,
                spec,
                path,
                available_variables,
                default_headers,
                initial_variables,
            )
            issues.extend(step_issues)
            if operation is not None and section_name != "cleanup":
                step_models[step.id] = (step, operation)
                step_positions[step.id] = len(step_positions)
            available_variables.update(extraction.variable for extraction in step.extract)

    for relation_index, relation in enumerate(scenario.relations):
        path = f"{collection_path}[{scenario_index}].relations[{relation_index}]"
        issues.extend(
            _validate_scenario_relation(relation, step_models, step_positions, spec, path)
        )
    return issues


def validate_plan_semantics(plan: TestPlan, spec: OpenAPISpec) -> list[SemanticIssue]:
    """Return every semantic mismatch between a structurally valid plan and spec."""
    issues: list[SemanticIssue] = []
    if plan.target.spec_id != spec.spec_id:
        issues.append(
            _issue(
                "spec_id_mismatch",
                "target.spec_id",
                f"plan targets {plan.target.spec_id!r}, loaded spec is {spec.spec_id!r}",
            )
        )
    for scenario_index, scenario in enumerate(plan.scenarios):
        issues.extend(
            _validate_scenario(
                scenario,
                scenario_index,
                spec,
                collection_path="scenarios",
                default_headers=plan.defaults.headers,
                initial_variables=plan.variables,
            )
        )
    return issues


def validate_test_case_batch_semantics(
    batch: TestCaseBatch,
    spec: OpenAPISpec,
    *,
    config: ExecutionConfig | None = None,
) -> list[SemanticIssue]:
    """Return every semantic mismatch between runner-ready cases and a spec."""
    issues: list[SemanticIssue] = []
    actual_config = config or ExecutionConfig()
    for case_index, case in enumerate(batch.cases):
        issues.extend(
            _validate_scenario(
                case,
                case_index,
                spec,
                collection_path="cases",
                default_headers=actual_config.headers,
                initial_variables=actual_config.initial_variables,
            )
        )
    return issues
