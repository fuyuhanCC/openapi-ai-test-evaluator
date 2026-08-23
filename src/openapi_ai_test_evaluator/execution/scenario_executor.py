"""Execute setup and primary steps within an isolated scenario variable scope."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.execution import (
    ExecutionOutcome,
    RelationResult,
    StepPhase,
)
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.domain.test_case import CleanupWhen, ExecutionConfig, TestCase
from openapi_ai_test_evaluator.execution.openapi_validation import OpenAPIContractValidator
from openapi_ai_test_evaluator.execution.scenario_relations import (
    execute_scenario_relations,
)
from openapi_ai_test_evaluator.execution.step_executor import (
    StepExecution,
    execute_step,
    skip_step,
)
from openapi_ai_test_evaluator.execution.transport import HttpTransport


@dataclass(frozen=True, slots=True)
class ScenarioMainExecution:
    """Completed setup/main executions before cleanup and relation evaluation."""

    scenario_id: str
    step_executions: tuple[StepExecution, ...]
    variables: Mapping[str, JsonValue] = field(repr=False)
    halted_after_step: str | None

    @property
    def completed(self) -> bool:
        """Return whether every declared setup and main step ran."""
        return self.halted_after_step is None


@dataclass(frozen=True, slots=True)
class ScenarioFlowExecution:
    """Completed setup, main, relation, and cleanup execution for one scenario."""

    main: ScenarioMainExecution
    relation_results: tuple[RelationResult, ...]
    cleanup_executions: tuple[StepExecution, ...]
    variables: Mapping[str, JsonValue] = field(repr=False)

    @property
    def step_executions(self) -> tuple[StepExecution, ...]:
        """Return every setup, main, and cleanup execution in declaration order."""
        return (*self.main.step_executions, *self.cleanup_executions)


def execute_scenario_main(
    scenario: TestCase,
    initial_variables: Mapping[str, JsonValue],
    spec: OpenAPISpec,
    defaults: ExecutionConfig,
    validator: OpenAPIContractValidator,
    transport: HttpTransport,
) -> ScenarioMainExecution:
    """Run setup and main steps serially in a scenario-local variable scope."""
    variables = dict(initial_variables)
    executions: list[StepExecution] = []

    for phase, steps in (
        (StepPhase.SETUP, scenario.setup),
        (StepPhase.MAIN, scenario.steps),
    ):
        for step in steps:
            execution = execute_step(
                step,
                phase,
                spec,
                defaults,
                variables,
                validator,
                transport,
            )
            executions.append(execution)
            variables.update(execution.extracted_values)

            if execution.result.outcome is not ExecutionOutcome.PASSED:
                return _result(scenario.id, executions, variables, step.id)

    return _result(scenario.id, executions, variables, None)


def execute_scenario_flow(
    scenario: TestCase,
    initial_variables: Mapping[str, JsonValue],
    spec: OpenAPISpec,
    defaults: ExecutionConfig,
    validator: OpenAPIContractValidator,
    transport: HttpTransport,
) -> ScenarioFlowExecution:
    """Execute setup/main steps and every conditionally eligible cleanup step."""
    main = execute_scenario_main(
        scenario,
        initial_variables,
        spec,
        defaults,
        validator,
        transport,
    )
    relation_results = execute_scenario_relations(
        scenario.relations,
        main.step_executions,
    )
    variables = dict(main.variables)
    cleanup_executions: list[StepExecution] = []

    for step in scenario.cleanup:
        if _cleanup_should_run(step.when, main.completed):
            execution = execute_step(
                step,
                StepPhase.CLEANUP,
                spec,
                defaults,
                variables,
                validator,
                transport,
            )
            variables.update(execution.extracted_values)
        else:
            execution = skip_step(step, StepPhase.CLEANUP)
        cleanup_executions.append(execution)

    return ScenarioFlowExecution(
        main=main,
        relation_results=relation_results,
        cleanup_executions=tuple(cleanup_executions),
        variables=MappingProxyType(dict(variables)),
    )


def _result(
    scenario_id: str,
    executions: list[StepExecution],
    variables: dict[str, JsonValue],
    halted_after_step: str | None,
) -> ScenarioMainExecution:
    return ScenarioMainExecution(
        scenario_id=scenario_id,
        step_executions=tuple(executions),
        variables=MappingProxyType(dict(variables)),
        halted_after_step=halted_after_step,
    )


def _cleanup_should_run(when: CleanupWhen, main_completed: bool) -> bool:
    if when is CleanupWhen.ALWAYS:
        return True
    if when is CleanupWhen.ON_SUCCESS:
        return main_completed
    return not main_completed
