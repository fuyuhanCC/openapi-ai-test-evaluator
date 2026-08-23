"""Aggregate execution intermediates into stable RunResult contracts."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from openapi_ai_test_evaluator.domain.execution import (
    ExecutionOutcome,
    FaultObservation,
    OutcomePolicy,
    RelationOutcome,
    RelationResult,
    RunResult,
    ScenarioResult,
    StepPhase,
    StepResult,
)
from openapi_ai_test_evaluator.domain.test_case import TestCase
from openapi_ai_test_evaluator.execution.scenario_executor import ScenarioFlowExecution
from openapi_ai_test_evaluator.execution.step_executor import skip_step


def aggregate_scenario_result(
    scenario: TestCase,
    execution: ScenarioFlowExecution,
) -> ScenarioResult:
    """Create a complete scenario artifact, including unexecuted skipped steps."""
    steps = _complete_step_results(scenario, execution)
    relations = list(execution.relation_results)
    return ScenarioResult(
        scenario_id=scenario.id,
        outcome=_scenario_outcome(steps, relations),
        steps=steps,
        relations=relations,
        errors=[],
    )


def aggregate_run_result(
    *,
    run_id: str,
    plan_name: str,
    spec_id: str,
    started_at: datetime,
    finished_at: datetime,
    duration_ms: int,
    scenarios: Sequence[ScenarioResult],
    fault: FaultObservation,
) -> RunResult:
    """Create the top-level raw result from completed scenario artifacts."""
    scenario_results = list(scenarios)
    return RunResult(
        schema_version="1.0",
        kind="RunResult",
        run_id=run_id,
        plan_name=plan_name,
        spec_id=spec_id,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        outcome=_aggregate_outcomes(
            [scenario.outcome for scenario in scenario_results],
        ),
        fault=fault,
        scenarios=scenario_results,
        errors=[],
    )


def _complete_step_results(
    scenario: TestCase,
    execution: ScenarioFlowExecution,
) -> list[StepResult]:
    actual = {
        step_execution.result.step_id: step_execution.result
        for step_execution in execution.step_executions
    }
    results: list[StepResult] = []
    for phase, declared_steps in (
        (StepPhase.SETUP, scenario.setup),
        (StepPhase.MAIN, scenario.steps),
        (StepPhase.CLEANUP, scenario.cleanup),
    ):
        for step in declared_steps:
            result = actual.get(step.id)
            results.append(result if result is not None else skip_step(step, phase).result)
    return results


def _scenario_outcome(
    steps: Sequence[StepResult],
    relations: Sequence[RelationResult],
) -> ExecutionOutcome:
    step_outcomes = [
        step.outcome
        for step in steps
        if step.outcome_policy is OutcomePolicy.REQUIRED
        and step.outcome is not ExecutionOutcome.SKIPPED
    ]
    relation_outcomes = [_relation_execution_outcome(relation.outcome) for relation in relations]
    return _aggregate_outcomes([*step_outcomes, *relation_outcomes])


def _relation_execution_outcome(outcome: RelationOutcome) -> ExecutionOutcome:
    if outcome is RelationOutcome.ERROR:
        return ExecutionOutcome.ERROR
    if outcome is RelationOutcome.FAILED:
        return ExecutionOutcome.FAILED
    if outcome is RelationOutcome.PASSED:
        return ExecutionOutcome.PASSED
    return ExecutionOutcome.SKIPPED


def _aggregate_outcomes(outcomes: Sequence[ExecutionOutcome]) -> ExecutionOutcome:
    if ExecutionOutcome.ERROR in outcomes:
        return ExecutionOutcome.ERROR
    if ExecutionOutcome.FAILED in outcomes:
        return ExecutionOutcome.FAILED
    if ExecutionOutcome.PASSED in outcomes:
        return ExecutionOutcome.PASSED
    return ExecutionOutcome.SKIPPED
