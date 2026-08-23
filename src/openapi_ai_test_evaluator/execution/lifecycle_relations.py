"""Evaluate the three allowlisted V1 lifecycle consistency relations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from openapi_ai_test_evaluator.domain.execution import (
    ComparisonOperator,
    ErrorCategory,
    RelationComparisonResult,
    RelationResult,
)
from openapi_ai_test_evaluator.domain.test_case import (
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
    select_relation_value,
)
from openapi_ai_test_evaluator.execution.step_executor import StepExecution

_ERROR_CATEGORY = ErrorCategory.LIFECYCLE_CONSISTENCY_VIOLATED


def execute_lifecycle_relations(
    relations: Sequence[ScenarioRelation],
    executions: Sequence[StepExecution],
) -> tuple[RelationResult, ...]:
    """Evaluate every lifecycle relation in declaration order."""
    by_step_id = {execution.result.step_id: execution for execution in executions}
    return tuple(
        execute_lifecycle_relation(relation, by_step_id)
        for relation in relations
        if relation.kind is RelationKind.LIFECYCLE
    )


def execute_lifecycle_relation(
    relation: ScenarioRelation,
    executions: Mapping[str, StepExecution],
) -> RelationResult:
    """Evaluate one supported lifecycle relation over completed step traces."""
    if relation.kind is not RelationKind.LIFECYCLE:
        raise ValueError(f"{relation.type.value!r} is not a lifecycle relation")

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

    if relation.type is RelationType.CREATE_READ:
        return _execute_field_pairs(relation, source, follow_up)
    if relation.type is RelationType.UPDATE_READ:
        return _execute_update_read(relation, source, follow_up, executions)
    if relation.type is RelationType.DELETE_READ:
        return _execute_delete_read(relation, source, follow_up)
    raise ValueError(f"unsupported lifecycle relation {relation.type.value!r}")


def _applicability_failure(
    relation: ScenarioRelation,
    source: StepExecution,
    follow_up: StepExecution,
) -> str | None:
    assert source.prepared_request is not None
    assert follow_up.prepared_request is not None

    if relation.type is RelationType.CREATE_READ:
        extracted_path_values = {
            serialized
            for _, value in source.extracted_values
            if (serialized := _serialize_runtime_scalar(value)) is not None
        }
        follow_path_values = {value for _, value in follow_up.prepared_request.path_parameters}
        if extracted_path_values.isdisjoint(follow_path_values):
            return "resolved follow-up path does not use a value extracted by the create step"
        return None

    if source.prepared_request.path != follow_up.prepared_request.path:
        return "resolved source and follow-up paths identify different resources"
    return None


def _serialize_runtime_scalar(value: object) -> str | None:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def _execute_field_pairs(
    relation: ScenarioRelation,
    source: StepExecution,
    follow_up: StepExecution,
    *,
    comparisons: list[RelationComparisonResult] | None = None,
) -> RelationResult:
    results = list(comparisons or [])
    try:
        for pair in relation.field_pairs:
            source_value = select_relation_value(source, pair.source)
            follow_value = select_relation_value(follow_up, pair.follow_up)
            passed = json_values_equal(source_value.raw_value, follow_value.raw_value)
            results.append(
                build_relation_comparison(
                    len(results) + 1,
                    ComparisonOperator.EQUALS,
                    source_value,
                    follow_value,
                    passed,
                    None
                    if passed
                    else (
                        f"{pair.source.location} {pair.source.pointer!r} differs from "
                        f"{pair.follow_up.location} {pair.follow_up.pointer!r}"
                    ),
                )
            )
    except RelationValueSelectionError as error:
        return _error_result(relation, str(error), comparisons=results)

    return _evaluated_result(
        relation,
        results,
        "one or more lifecycle fields are inconsistent",
    )


def _execute_update_read(
    relation: ScenarioRelation,
    source: StepExecution,
    follow_up: StepExecution,
    executions: Mapping[str, StepExecution],
) -> RelationResult:
    comparisons: list[RelationComparisonResult] = []
    if relation.stable_follow_up_pointers:
        baseline = executions.get(relation.baseline_step or "")
        if baseline is None:
            return build_not_applicable_relation_result(
                relation,
                "the declared baseline step did not execute",
            )
        try:
            for pointer in relation.stable_follow_up_pointers:
                reference = RelationFieldReference(location="response.body", pointer=pointer)
                baseline_value = select_relation_value(baseline, reference)
                follow_value = select_relation_value(follow_up, reference)
                passed = json_values_equal(
                    baseline_value.raw_value,
                    follow_value.raw_value,
                )
                comparisons.append(
                    build_relation_comparison(
                        len(comparisons) + 1,
                        ComparisonOperator.UNCHANGED,
                        baseline_value,
                        follow_value,
                        passed,
                        None if passed else f"stable response field changed at {pointer!r}",
                    )
                )
        except RelationValueSelectionError as error:
            return _error_result(relation, str(error), comparisons=comparisons)

    return _execute_field_pairs(
        relation,
        source,
        follow_up,
        comparisons=comparisons,
    )


def _execute_delete_read(
    relation: ScenarioRelation,
    source: StepExecution,
    follow_up: StepExecution,
) -> RelationResult:
    status_reference = RelationFieldReference(location="response.status")
    try:
        source_status = select_relation_value(source, status_reference)
        follow_status = select_relation_value(follow_up, status_reference)
    except RelationValueSelectionError as error:
        return _error_result(relation, str(error))

    if not isinstance(source_status.raw_value, int) or not (200 <= source_status.raw_value < 300):
        return build_not_applicable_relation_result(
            relation,
            "source delete request did not return a successful 2xx status",
        )

    expected_statuses = list(relation.accepted_follow_up_statuses)
    passed = follow_status.raw_value in expected_statuses
    comparison = build_relation_comparison(
        1,
        ComparisonOperator.ONE_OF,
        source_status,
        follow_status,
        passed,
        None if passed else f"follow-up status is not one of {expected_statuses!r}",
        expected=expected_statuses,
    )
    return _evaluated_result(
        relation,
        [comparison],
        "deleted resource remained accessible with an unaccepted status",
    )


def _evaluated_result(
    relation: ScenarioRelation,
    comparisons: list[RelationComparisonResult],
    failure_message: str,
) -> RelationResult:
    return build_evaluated_relation_result(
        relation,
        comparisons,
        failure_message,
        _ERROR_CATEGORY,
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
        _ERROR_CATEGORY,
        comparisons=comparisons,
    )
