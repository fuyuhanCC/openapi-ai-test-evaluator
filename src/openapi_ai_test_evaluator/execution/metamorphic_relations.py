"""Evaluate the three allowlisted V1 metamorphic relations."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import cast

from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.execution import (
    ComparisonOperator,
    ErrorCategory,
    RelationComparisonResult,
    RelationResult,
)
from openapi_ai_test_evaluator.domain.test_plan import (
    PaginationMode,
    RelationFieldReference,
    RelationKind,
    RelationType,
    ScenarioRelation,
)
from openapi_ai_test_evaluator.execution.relation_results import (
    build_evaluated_relation_result,
    build_not_applicable_relation_result,
    build_relation_comparison,
    build_relation_error_result,
    json_values_equal,
)
from openapi_ai_test_evaluator.execution.relation_values import (
    RelationValueSelectionError,
    SelectedRelationValue,
    select_relation_value,
)
from openapi_ai_test_evaluator.execution.request_builder import PreparedRequest
from openapi_ai_test_evaluator.execution.response_selection import (
    ResponseSelectionError,
    pointer_tokens,
    select_json_pointer_value,
)
from openapi_ai_test_evaluator.execution.step_executor import StepExecution

_IGNORED = object()


def execute_metamorphic_relations(
    relations: Sequence[ScenarioRelation],
    executions: Sequence[StepExecution],
) -> tuple[RelationResult, ...]:
    """Evaluate every metamorphic relation in declaration order."""
    by_step_id = {execution.result.step_id: execution for execution in executions}
    return tuple(
        execute_metamorphic_relation(relation, by_step_id)
        for relation in relations
        if relation.kind is RelationKind.METAMORPHIC
    )


def execute_metamorphic_relation(
    relation: ScenarioRelation,
    executions: Mapping[str, StepExecution],
) -> RelationResult:
    """Evaluate one supported metamorphic relation over completed step traces."""
    if relation.kind is not RelationKind.METAMORPHIC:
        raise ValueError(f"{relation.type.value!r} is not a metamorphic relation")

    source = executions.get(relation.source_step)
    follow_up = executions.get(relation.follow_up_step)
    if source is None or follow_up is None:
        return build_not_applicable_relation_result(
            relation,
            "referenced steps did not both execute",
        )
    if source.prepared_request is None or follow_up.prepared_request is None:
        return _error_result(relation, "a referenced request was not prepared")

    applicability_reason = _applicability_failure(relation, source, follow_up)
    if applicability_reason is not None:
        return build_not_applicable_relation_result(relation, applicability_reason)

    if relation.type is RelationType.REPEATED_READ:
        return _execute_repeated_read(relation, source, follow_up)
    if relation.type is RelationType.QUERY_ORDER:
        return _execute_query_order(relation, source, follow_up)
    if relation.type is RelationType.PAGINATION:
        return _execute_pagination(relation, source, follow_up)
    raise ValueError(f"unsupported metamorphic relation {relation.type.value!r}")


def _applicability_failure(
    relation: ScenarioRelation,
    source: StepExecution,
    follow_up: StepExecution,
) -> str | None:
    assert source.prepared_request is not None
    assert follow_up.prepared_request is not None
    source_request = source.prepared_request
    follow_request = follow_up.prepared_request

    if relation.type is RelationType.REPEATED_READ:
        if source_request != follow_request:
            return "resolved repeated-read requests are not equivalent"
        return None

    if relation.type is RelationType.QUERY_ORDER:
        if not _requests_equal_except_query(source_request, follow_request):
            return "resolved requests differ outside query parameter order"
        if Counter(source_request.query) != Counter(follow_request.query):
            return "resolved query parameters do not contain the same names and values"
        if source_request.query == follow_request.query:
            return "resolved query parameter order did not change"
        return None

    if relation.type is RelationType.PAGINATION:
        if not _requests_equal_except_query(source_request, follow_request):
            return "resolved pagination requests differ outside query parameters"
        return _pagination_applicability_failure(
            relation,
            source_request.query,
            follow_request.query,
        )
    return None


def _requests_equal_except_query(
    source: PreparedRequest,
    follow_up: PreparedRequest,
) -> bool:
    return (
        source.operation_id == follow_up.operation_id
        and source.method == follow_up.method
        and source.path == follow_up.path
        and source.path_parameters == follow_up.path_parameters
        and source.headers == follow_up.headers
        and source.json_body == follow_up.json_body
        and source.timeout_ms == follow_up.timeout_ms
    )


def _pagination_applicability_failure(
    relation: ScenarioRelation,
    source_query: tuple[tuple[str, str], ...],
    follow_query: tuple[tuple[str, str], ...],
) -> str | None:
    size_name = relation.page_size_parameter or ""
    source_sizes = [value for name, value in source_query if name == size_name]
    follow_sizes = [value for name, value in follow_query if name == size_name]
    source_context = Counter(item for item in source_query if item[0] != size_name)
    follow_context = Counter(item for item in follow_query if item[0] != size_name)

    if source_context != follow_context:
        return "resolved non-size pagination parameters differ"
    if len(source_sizes) != 1 or len(follow_sizes) != 1:
        return "resolved pagination requests require one page-size value each"
    try:
        source_size = Decimal(source_sizes[0])
        follow_size = Decimal(follow_sizes[0])
    except InvalidOperation:
        return "resolved page-size values are not numeric"
    if not source_size.is_finite() or not follow_size.is_finite():
        return "resolved page-size values must be finite"
    if follow_size <= source_size:
        return "resolved follow-up page size is not larger than the source"
    return None


def _execute_repeated_read(
    relation: ScenarioRelation,
    source: StepExecution,
    follow_up: StepExecution,
) -> RelationResult:
    comparisons: list[RelationComparisonResult] = []
    try:
        for pointer in relation.compare_pointers:
            source_value = _response_body_value(source, pointer)
            follow_value = _response_body_value(follow_up, pointer)
            normalized_source = _normalized_repeated_value(
                source_value.raw_value,
                pointer,
                relation.ignore_pointers,
            )
            normalized_follow = _normalized_repeated_value(
                follow_value.raw_value,
                pointer,
                relation.ignore_pointers,
            )
            if normalized_source is _IGNORED or normalized_follow is _IGNORED:
                continue
            passed = json_values_equal(normalized_source, normalized_follow)
            comparisons.append(
                build_relation_comparison(
                    len(comparisons) + 1,
                    ComparisonOperator.EQUALS,
                    source_value,
                    follow_value,
                    passed,
                    None if passed else f"response values differ at {pointer!r}",
                )
            )
    except (RelationValueSelectionError, ResponseSelectionError) as error:
        return _error_result(relation, str(error), comparisons=comparisons)

    if not comparisons:
        return _error_result(
            relation,
            "no effective repeated-read comparison pointers remain after ignores",
        )
    return _evaluated_result(
        relation,
        comparisons,
        "one or more stable response values changed",
    )


def _execute_query_order(
    relation: ScenarioRelation,
    source: StepExecution,
    follow_up: StepExecution,
) -> RelationResult:
    try:
        source_collection = _collection_value(source, relation.collection_pointer or "")
        follow_collection = _collection_value(follow_up, relation.collection_pointer or "")
        source_keys = _collection_keys(
            source_collection.raw_value,
            relation.item_key_pointer or "",
        )
        follow_keys = _collection_keys(
            follow_collection.raw_value,
            relation.item_key_pointer or "",
        )
        passed = {_canonical_key(value) for value in source_keys} == {
            _canonical_key(value) for value in follow_keys
        }
    except (RelationValueSelectionError, ResponseSelectionError) as error:
        return _error_result(relation, str(error))

    comparison = build_relation_comparison(
        1,
        ComparisonOperator.SET_EQUALS,
        source_collection,
        follow_collection,
        passed,
        None if passed else "response collections contain different item-key sets",
    )
    return _evaluated_result(
        relation,
        [comparison],
        "query order changed the response item-key set",
    )


def _execute_pagination(
    relation: ScenarioRelation,
    source: StepExecution,
    follow_up: StepExecution,
) -> RelationResult:
    try:
        source_collection = _collection_value(source, relation.collection_pointer or "")
        follow_collection = _collection_value(follow_up, relation.collection_pointer or "")
        source_keys = [
            _canonical_key(value)
            for value in _collection_keys(
                source_collection.raw_value,
                relation.item_key_pointer or "",
            )
        ]
        follow_keys = [
            _canonical_key(value)
            for value in _collection_keys(
                follow_collection.raw_value,
                relation.item_key_pointer or "",
            )
        ]
    except (RelationValueSelectionError, ResponseSelectionError) as error:
        return _error_result(relation, str(error))

    if relation.mode is PaginationMode.SUBSET:
        operator = ComparisonOperator.SUBSET
        passed = set(source_keys).issubset(follow_keys)
        failure_message = "source page item keys are not a subset of the larger page"
    else:
        operator = ComparisonOperator.PREFIX
        passed = source_keys == follow_keys[: len(source_keys)]
        failure_message = "source page item keys are not a prefix of the larger page"

    comparison = build_relation_comparison(
        1,
        operator,
        source_collection,
        follow_collection,
        passed,
        None if passed else failure_message,
    )
    return _evaluated_result(relation, [comparison], failure_message)


def _response_body_value(execution: StepExecution, pointer: str) -> SelectedRelationValue:
    return select_relation_value(
        execution,
        RelationFieldReference(location="response.body", pointer=pointer),
    )


def _collection_value(execution: StepExecution, pointer: str) -> SelectedRelationValue:
    selected = _response_body_value(execution, pointer)
    if not isinstance(selected.raw_value, list):
        raise RelationValueSelectionError(f"response collection at {pointer!r} is not an array")
    return selected


def _collection_keys(collection: JsonValue, item_pointer: str) -> list[JsonValue]:
    assert isinstance(collection, list)
    keys: list[JsonValue] = []
    for index, item in enumerate(collection):
        selection = select_json_pointer_value(item, item_pointer)
        if not selection.found:
            raise RelationValueSelectionError(
                f"collection item {index} lacks key at {item_pointer!r}"
            )
        keys.append(cast(JsonValue, selection.value))
    return keys


def _canonical_key(value: JsonValue) -> tuple[object, ...]:
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        try:
            number = Decimal(str(value))
        except InvalidOperation as error:
            raise RelationValueSelectionError("item key is not canonical JSON") from error
        if not number.is_finite():
            raise RelationValueSelectionError("item key is not canonical JSON")
        return ("number", number)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, list):
        return ("array", tuple(_canonical_key(item) for item in value))
    if isinstance(value, dict):
        return (
            "object",
            tuple((key, _canonical_key(value[key])) for key in sorted(value)),
        )
    raise RelationValueSelectionError("item key is not canonical JSON")


def _normalized_repeated_value(
    value: JsonValue,
    compare_pointer: str,
    ignore_pointers: Sequence[str],
) -> object:
    compare_tokens = pointer_tokens(compare_pointer)
    normalized: object = deepcopy(value)
    for ignore_pointer in ignore_pointers:
        ignore_tokens = pointer_tokens(ignore_pointer)
        if compare_tokens[: len(ignore_tokens)] == ignore_tokens:
            return _IGNORED
        if ignore_tokens[: len(compare_tokens)] != compare_tokens:
            continue
        _remove_relative_pointer(normalized, ignore_tokens[len(compare_tokens) :])
    return normalized


def _remove_relative_pointer(value: object, tokens: list[str]) -> None:
    if not tokens:
        return
    current = value
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                return
            current = current[token]
        elif isinstance(current, list):
            index = _array_index(token)
            if index is None or index >= len(current):
                return
            current = current[index]
        else:
            return

    final = tokens[-1]
    if isinstance(current, dict):
        current.pop(final, None)
    elif isinstance(current, list):
        index = _array_index(final)
        if index is not None and index < len(current):
            current[index] = _IGNORED


def _array_index(token: str) -> int | None:
    if token == "0":
        return 0
    if not token.isascii() or not token.isdigit() or token.startswith("0"):
        return None
    return int(token)


def _evaluated_result(
    relation: ScenarioRelation,
    comparisons: list[RelationComparisonResult],
    failure_message: str,
) -> RelationResult:
    return build_evaluated_relation_result(
        relation,
        comparisons,
        failure_message,
        ErrorCategory.METAMORPHIC_RELATION_VIOLATED,
    )


def _error_result(
    relation: ScenarioRelation,
    message: str,
    *,
    comparisons: list[RelationComparisonResult] | None = None,
) -> RelationResult:
    return build_relation_error_result(
        relation,
        message,
        ErrorCategory.METAMORPHIC_RELATION_VIOLATED,
        comparisons=comparisons,
    )
