"""Dispatch scenario relations while preserving TestPlan declaration order."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from openapi_ai_test_evaluator.domain.execution import RelationResult
from openapi_ai_test_evaluator.domain.test_plan import (
    RelationKind,
    ScenarioRelation,
)
from openapi_ai_test_evaluator.execution.lifecycle_relations import (
    execute_lifecycle_relation,
)
from openapi_ai_test_evaluator.execution.metamorphic_relations import (
    execute_metamorphic_relation,
)
from openapi_ai_test_evaluator.execution.step_executor import StepExecution


def execute_scenario_relations(
    relations: Sequence[ScenarioRelation],
    executions: Sequence[StepExecution],
) -> tuple[RelationResult, ...]:
    """Evaluate every declared relation in declaration order."""
    by_step_id = {execution.result.step_id: execution for execution in executions}
    return tuple(execute_scenario_relation(relation, by_step_id) for relation in relations)


def execute_scenario_relation(
    relation: ScenarioRelation,
    executions: Mapping[str, StepExecution],
) -> RelationResult:
    """Dispatch one relation to its deterministic evaluator."""
    if relation.kind is RelationKind.METAMORPHIC:
        return execute_metamorphic_relation(relation, executions)
    return execute_lifecycle_relation(relation, executions)
