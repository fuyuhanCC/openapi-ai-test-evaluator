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
    StepPhase,
    StepResult,
    TestCaseResult,
)
from openapi_ai_test_evaluator.domain.test_case import TestCase
from openapi_ai_test_evaluator.execution.scenario_executor import ScenarioFlowExecution
from openapi_ai_test_evaluator.execution.step_executor import skip_step


def aggregate_test_case_result(
    test_case: TestCase,
    execution: ScenarioFlowExecution,
) -> TestCaseResult:
    """Create a complete test-case artifact, including unexecuted skipped steps."""
    steps = _complete_step_results(test_case, execution)
    relations = list(execution.relation_results)
    return TestCaseResult(
        case_id=test_case.id,
        outcome=_test_case_outcome(steps, relations),
        steps=steps,
        relations=relations,
        errors=[],
    )


def aggregate_run_result(
    *,
    run_id: str,
    batch_name: str,
    spec_id: str,
    started_at: datetime,
    finished_at: datetime,
    duration_ms: int,
    cases: Sequence[TestCaseResult],
    fault: FaultObservation,
) -> RunResult:
    """Create the top-level raw result from completed test-case artifacts."""
    case_results = list(cases)
    return RunResult(
        schema_version="2.0",
        kind="RunResult",
        run_id=run_id,
        batch_name=batch_name,
        spec_id=spec_id,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        outcome=_aggregate_outcomes(
            [case.outcome for case in case_results],
        ),
        fault=fault,
        cases=case_results,
        errors=[],
    )


def _complete_step_results(
    test_case: TestCase,
    execution: ScenarioFlowExecution,
) -> list[StepResult]:
    actual = {
        step_execution.result.step_id: step_execution.result
        for step_execution in execution.step_executions
    }
    results: list[StepResult] = []
    for phase, declared_steps in (
        (StepPhase.SETUP, test_case.setup),
        (StepPhase.MAIN, test_case.steps),
        (StepPhase.CLEANUP, test_case.cleanup),
    ):
        for step in declared_steps:
            result = actual.get(step.id)
            results.append(result if result is not None else skip_step(step, phase).result)
    return results


def _test_case_outcome(
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
