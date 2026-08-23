"""Execute a validated TestPlan and return one complete RunResult."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from time import perf_counter_ns
from uuid import uuid4

import httpx
from pydantic import TypeAdapter, ValidationError

from openapi_ai_test_evaluator.domain.contracts import Identifier
from openapi_ai_test_evaluator.domain.execution import (
    FaultObservation,
    FaultTriggerStatus,
    RunResult,
)
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.domain.test_case import ExecutionConfig, TestCase
from openapi_ai_test_evaluator.domain.test_plan import TestPlan
from openapi_ai_test_evaluator.execution.openapi_validation import OpenAPIContractValidator
from openapi_ai_test_evaluator.execution.result_aggregator import (
    aggregate_run_result,
    aggregate_scenario_result,
)
from openapi_ai_test_evaluator.execution.scenario_executor import execute_scenario_flow
from openapi_ai_test_evaluator.execution.transport import (
    DEFAULT_MAX_RESPONSE_BYTES,
    HttpTransport,
)
from openapi_ai_test_evaluator.validation.semantic_validator import (
    SemanticIssue,
    validate_plan_semantics,
)


class PlanExecutionRejected(ValueError):
    """A plan failed semantic validation and was not allowed to send requests."""

    def __init__(self, issues: list[SemanticIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(f"TestPlan has {len(issues)} semantic issue(s)")


class MutationExecutionRejected(ValueError):
    """A mutating plan lacked explicit isolated-environment authorization."""

    def __init__(self, operation_ids: list[str]) -> None:
        self.operation_ids = tuple(operation_ids)
        operations = ", ".join(operation_ids)
        super().__init__(
            "mutating operations require explicit authorization for an isolated "
            f"test target: {operations}"
        )


_RUN_ID_ADAPTER = TypeAdapter(Identifier)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def execute_test_plan(
    plan: TestPlan,
    spec: OpenAPISpec,
    base_url: str,
    *,
    run_id: str | None = None,
    fault: FaultObservation | None = None,
    allow_mutations: bool = False,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    httpx_transport: httpx.BaseTransport | None = None,
) -> RunResult:
    """Execute every scenario serially with isolated variables and shared transport."""
    if semantic_issues := validate_plan_semantics(plan, spec):
        raise PlanExecutionRejected(semantic_issues)
    config = ExecutionConfig(
        timeout_ms=plan.defaults.timeout_ms,
        headers=plan.defaults.headers,
        initial_variables=plan.variables,
    )
    return execute_validated_cases(
        plan.scenarios,
        spec,
        base_url,
        run_name=plan.metadata.name,
        config=config,
        run_id=run_id,
        fault=fault,
        allow_mutations=allow_mutations,
        max_response_bytes=max_response_bytes,
        httpx_transport=httpx_transport,
    )


def execute_validated_cases(
    cases: Sequence[TestCase],
    spec: OpenAPISpec,
    base_url: str,
    *,
    run_name: str,
    config: ExecutionConfig,
    run_id: str | None = None,
    fault: FaultObservation | None = None,
    allow_mutations: bool = False,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    httpx_transport: httpx.BaseTransport | None = None,
) -> RunResult:
    """Execute cases whose OpenAPI semantics were already validated by their caller."""
    mutating_operations = _mutating_operation_ids(cases, spec)
    if mutating_operations and not allow_mutations:
        raise MutationExecutionRejected(mutating_operations)
    normalized_base_url = validate_base_url(base_url)
    try:
        actual_run_id = _RUN_ID_ADAPTER.validate_python(run_id or f"run-{uuid4().hex}")
    except ValidationError as error:
        raise ValueError("run ID must use lowercase letters, digits, and hyphens") from error
    fault_observation = fault or FaultObservation(
        configured_fault_id=None,
        trigger_status=FaultTriggerStatus.NOT_CONFIGURED,
        trigger_count=0,
    )
    started_at = _utc_now()
    started_timer = perf_counter_ns()
    validator = OpenAPIContractValidator(spec, normalized_base_url)
    scenario_results = []

    with HttpTransport(
        normalized_base_url,
        max_response_bytes=max_response_bytes,
        transport=httpx_transport,
    ) as transport:
        for scenario in cases:
            execution = execute_scenario_flow(
                scenario,
                config.initial_variables,
                spec,
                config,
                validator,
                transport,
            )
            scenario_results.append(aggregate_scenario_result(scenario, execution))

    finished_at = _utc_now()
    return aggregate_run_result(
        run_id=actual_run_id,
        plan_name=run_name,
        spec_id=spec.spec_id,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=_elapsed_ms(started_timer),
        scenarios=scenario_results,
        fault=fault_observation,
    )


def validate_base_url(base_url: str) -> str:
    """Accept one explicit HTTP(S) target without credentials, query, or fragment."""
    try:
        parsed = httpx.URL(base_url)
    except (TypeError, ValueError) as error:
        raise ValueError("base URL is invalid") from error
    if parsed.scheme not in {"http", "https"} or parsed.host is None:
        raise ValueError("base URL must be an absolute http:// or https:// URL")
    if parsed.username or parsed.password:
        raise ValueError("base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain a query string or fragment")
    return str(parsed).rstrip("/")


def _mutating_operation_ids(
    cases: Sequence[TestCase],
    spec: OpenAPISpec,
) -> list[str]:
    operation_ids: list[str] = []
    for scenario in cases:
        for step in (*scenario.setup, *scenario.steps, *scenario.cleanup):
            operation = spec.operations[step.operation_id]
            if operation.method not in _SAFE_METHODS and step.operation_id not in operation_ids:
                operation_ids.append(step.operation_id)
    return operation_ids


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _elapsed_ms(started_at: int) -> int:
    return max(0, (perf_counter_ns() - started_at) // 1_000_000)
