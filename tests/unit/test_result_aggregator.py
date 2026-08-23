from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from openapi_ai_test_evaluator.domain.execution import (
    ComparisonOperator,
    ExecutionOutcome,
    FaultObservation,
    FaultTriggerStatus,
    OutcomePolicy,
    RelationComparisonResult,
    RelationOutcome,
    RelationResult,
    RelationValueSnapshot,
    StepPhase,
    StepResult,
)
from openapi_ai_test_evaluator.domain.execution import TestCaseResult as CaseResult
from openapi_ai_test_evaluator.domain.test_plan import Scenario
from openapi_ai_test_evaluator.execution import (
    ScenarioFlowExecution,
    ScenarioMainExecution,
    StepExecution,
    aggregate_run_result,
    aggregate_test_case_result,
)


def step_execution(
    step_id: str,
    outcome: ExecutionOutcome,
    *,
    phase: StepPhase = StepPhase.MAIN,
    policy: OutcomePolicy = OutcomePolicy.REQUIRED,
) -> StepExecution:
    return StepExecution(
        result=StepResult(
            phase=phase,
            step_id=step_id,
            operation_id="listItems",
            outcome_policy=policy,
            outcome=outcome,
            duration_ms=1,
            retry_count=0,
            request=None,
            response=None,
            extractions=[],
            assertions=[],
            errors=[],
        ),
        extracted_values=(),
        prepared_request=None,
        processed_response=None,
    )


def flow(
    scenario_id: str,
    main_steps: list[StepExecution],
    *,
    relations: list[RelationResult] | None = None,
    cleanup: list[StepExecution] | None = None,
    halted_after_step: str | None = None,
) -> ScenarioFlowExecution:
    variables = MappingProxyType({})
    return ScenarioFlowExecution(
        main=ScenarioMainExecution(
            scenario_id=scenario_id,
            step_executions=tuple(main_steps),
            variables=variables,
            halted_after_step=halted_after_step,
        ),
        relation_results=tuple(relations or []),
        cleanup_executions=tuple(cleanup or []),
        variables=variables,
    )


def not_applicable_relation() -> RelationResult:
    return RelationResult(
        relation_id="repeat",
        kind="metamorphic",
        type="repeated_read_consistency",
        source_step="first",
        follow_up_step="second",
        baseline_step=None,
        outcome=RelationOutcome.NOT_APPLICABLE,
        message="follow-up did not execute",
        comparisons=[],
        errors=[],
    )


def relation_with_outcome(outcome: RelationOutcome) -> RelationResult:
    if outcome is RelationOutcome.NOT_APPLICABLE:
        return not_applicable_relation()
    comparisons = []
    if outcome is RelationOutcome.FAILED:
        comparisons = [
            RelationComparisonResult(
                comparison_id="comparison-1",
                operator=ComparisonOperator.EQUALS,
                outcome=ExecutionOutcome.FAILED,
                source=RelationValueSnapshot(
                    step_id="first",
                    location="response.body",
                    pointer="",
                    value=1,
                ),
                follow_up=RelationValueSnapshot(
                    step_id="second",
                    location="response.body",
                    pointer="",
                    value=2,
                ),
                expected=None,
                message="values differ",
            )
        ]
    return RelationResult(
        relation_id="repeat",
        kind="metamorphic",
        type="repeated_read_consistency",
        source_step="first",
        follow_up_step="second",
        baseline_step=None,
        outcome=outcome,
        message="relation failed",
        comparisons=comparisons,
        errors=[],
    )


def test_fills_unexecuted_required_steps_with_explicit_skipped_results() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "halted",
            "steps": [
                {"id": "first", "operation_id": "listItems"},
                {"id": "second", "operation_id": "listItems"},
            ],
            "relations": [
                {
                    "id": "repeat",
                    "type": "repeated_read_consistency",
                    "source_step": "first",
                    "follow_up_step": "second",
                    "compare_pointers": [""],
                }
            ],
        }
    )
    execution = flow(
        scenario.id,
        [step_execution("first", ExecutionOutcome.FAILED)],
        relations=[not_applicable_relation()],
        halted_after_step="first",
    )

    result = aggregate_test_case_result(scenario, execution)

    assert result.outcome is ExecutionOutcome.FAILED
    assert [step.step_id for step in result.steps] == ["first", "second"]
    assert [step.outcome for step in result.steps] == [
        ExecutionOutcome.FAILED,
        ExecutionOutcome.SKIPPED,
    ]
    assert result.relations[0].outcome is RelationOutcome.NOT_APPLICABLE


@pytest.mark.parametrize(
    ("ignore_errors", "expected"),
    [(True, ExecutionOutcome.PASSED), (False, ExecutionOutcome.FAILED)],
)
def test_cleanup_outcome_policy_controls_parent_propagation(
    ignore_errors: bool,
    expected: ExecutionOutcome,
) -> None:
    scenario = Scenario.model_validate(
        {
            "id": "cleanup",
            "steps": [{"id": "main", "operation_id": "listItems"}],
            "cleanup": [
                {
                    "id": "cleanup-step",
                    "operation_id": "listItems",
                    "ignore_errors": ignore_errors,
                }
            ],
        }
    )
    policy = OutcomePolicy.BEST_EFFORT if ignore_errors else OutcomePolicy.REQUIRED
    execution = flow(
        scenario.id,
        [step_execution("main", ExecutionOutcome.PASSED)],
        cleanup=[
            step_execution(
                "cleanup-step",
                ExecutionOutcome.FAILED,
                phase=StepPhase.CLEANUP,
                policy=policy,
            )
        ],
    )

    result = aggregate_test_case_result(scenario, execution)

    assert result.outcome is expected
    assert result.steps[-1].outcome is ExecutionOutcome.FAILED


@pytest.mark.parametrize(
    ("relation_outcome", "expected"),
    [
        (RelationOutcome.ERROR, ExecutionOutcome.ERROR),
        (RelationOutcome.FAILED, ExecutionOutcome.FAILED),
        (RelationOutcome.NOT_APPLICABLE, ExecutionOutcome.PASSED),
    ],
)
def test_relation_outcomes_propagate_with_explicit_priority(
    relation_outcome: RelationOutcome,
    expected: ExecutionOutcome,
) -> None:
    scenario = Scenario.model_validate(
        {
            "id": "relations",
            "steps": [
                {"id": "first", "operation_id": "listItems"},
                {"id": "second", "operation_id": "listItems"},
            ],
        }
    )
    relation = relation_with_outcome(relation_outcome)
    execution = flow(
        scenario.id,
        [
            step_execution("first", ExecutionOutcome.PASSED),
            step_execution("second", ExecutionOutcome.PASSED),
        ],
        relations=[relation],
    )

    result = aggregate_test_case_result(scenario, execution)

    assert result.outcome is expected


def test_run_outcome_uses_error_then_failed_then_passed_priority() -> None:
    cases = [
        CaseResult(
            case_id="passed",
            outcome=ExecutionOutcome.PASSED,
            steps=[step_execution("pass-step", ExecutionOutcome.PASSED).result],
            relations=[],
            errors=[],
        ),
        CaseResult(
            case_id="failed",
            outcome=ExecutionOutcome.FAILED,
            steps=[step_execution("fail-step", ExecutionOutcome.FAILED).result],
            relations=[],
            errors=[],
        ),
        CaseResult(
            case_id="errored",
            outcome=ExecutionOutcome.ERROR,
            steps=[step_execution("error-step", ExecutionOutcome.ERROR).result],
            relations=[],
            errors=[],
        ),
    ]

    result = aggregate_run_result(
        run_id="run-test",
        batch_name="test-batch",
        spec_id="demo-items-v1",
        started_at=datetime(2026, 8, 23, 10, tzinfo=UTC),
        finished_at=datetime(2026, 8, 23, 10, 0, 1, tzinfo=UTC),
        duration_ms=1000,
        cases=cases,
        fault=FaultObservation(
            configured_fault_id=None,
            trigger_status=FaultTriggerStatus.NOT_CONFIGURED,
            trigger_count=0,
        ),
    )

    assert result.outcome is ExecutionOutcome.ERROR
    assert result.duration_ms == 1000
