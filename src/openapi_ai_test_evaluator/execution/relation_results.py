"""Shared comparison and result construction for scenario relations."""

from __future__ import annotations

from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.execution import (
    ComparisonOperator,
    ErrorCategory,
    ExecutionOutcome,
    RelationComparisonResult,
    RelationOutcome,
    RelationResult,
    StructuredError,
)
from openapi_ai_test_evaluator.domain.test_case import ScenarioRelation
from openapi_ai_test_evaluator.execution.relation_values import SelectedRelationValue


def json_values_equal(left: object, right: object) -> bool:
    """Compare JSON-like values without treating booleans as numbers."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if _is_number(left) and _is_number(right):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            json_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            json_values_equal(left[key], right[key]) for key in left
        )
    return type(left) is type(right) and left == right


def build_relation_comparison(
    index: int,
    operator: ComparisonOperator,
    source: SelectedRelationValue,
    follow_up: SelectedRelationValue,
    passed: bool,
    message: str | None,
    *,
    expected: JsonValue | None = None,
) -> RelationComparisonResult:
    """Build one sanitized relation comparison result."""
    return RelationComparisonResult(
        comparison_id=f"comparison-{index}",
        operator=operator,
        outcome=ExecutionOutcome.PASSED if passed else ExecutionOutcome.FAILED,
        source=source.snapshot,
        follow_up=follow_up.snapshot,
        expected=expected,
        message=message,
    )


def build_evaluated_relation_result(
    relation: ScenarioRelation,
    comparisons: list[RelationComparisonResult],
    failure_message: str,
    category: ErrorCategory,
) -> RelationResult:
    """Build a passed or failed result from completed comparisons."""
    failed = any(comparison.outcome is ExecutionOutcome.FAILED for comparison in comparisons)
    return RelationResult(
        relation_id=relation.id,
        kind=relation.kind,
        type=relation.type,
        source_step=relation.source_step,
        follow_up_step=relation.follow_up_step,
        baseline_step=relation.baseline_step,
        outcome=RelationOutcome.FAILED if failed else RelationOutcome.PASSED,
        message=failure_message if failed else None,
        comparisons=comparisons,
        errors=([_relation_error(relation, failure_message, category)] if failed else []),
    )


def build_not_applicable_relation_result(
    relation: ScenarioRelation,
    message: str,
) -> RelationResult:
    """Build a relation result whose runtime preconditions were not met."""
    return RelationResult(
        relation_id=relation.id,
        kind=relation.kind,
        type=relation.type,
        source_step=relation.source_step,
        follow_up_step=relation.follow_up_step,
        baseline_step=relation.baseline_step,
        outcome=RelationOutcome.NOT_APPLICABLE,
        message=message,
        comparisons=[],
        errors=[],
    )


def build_relation_error_result(
    relation: ScenarioRelation,
    message: str,
    category: ErrorCategory,
    *,
    comparisons: list[RelationComparisonResult] | None = None,
) -> RelationResult:
    """Build a relation result when no deterministic verdict can be obtained."""
    return RelationResult(
        relation_id=relation.id,
        kind=relation.kind,
        type=relation.type,
        source_step=relation.source_step,
        follow_up_step=relation.follow_up_step,
        baseline_step=relation.baseline_step,
        outcome=RelationOutcome.ERROR,
        message=message,
        comparisons=comparisons or [],
        errors=[_relation_error(relation, message, category)],
    )


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _relation_error(
    relation: ScenarioRelation,
    message: str,
    category: ErrorCategory,
) -> StructuredError:
    return StructuredError(
        error_id="error-1",
        category=category,
        location=f"relations.{relation.id}",
        pointer=None,
        assertion_id=None,
        message=message,
        evidence=[],
    )
