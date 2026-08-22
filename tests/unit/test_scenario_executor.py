from pathlib import Path

import httpx
from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.execution import (
    ExecutionOutcome,
    OutcomePolicy,
    StepPhase,
)
from openapi_ai_test_evaluator.domain.test_plan import PlanDefaults, Scenario
from openapi_ai_test_evaluator.execution import (
    HttpTransport,
    OpenAPIContractValidator,
    execute_scenario_flow,
    execute_scenario_main,
)
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")
BASE_URL = "https://example.test/api"
DEFAULTS = PlanDefaults()


def valid_item(item_id: int = 7) -> dict[str, JsonValue]:
    return {
        "id": item_id,
        "name": "Created Item",
        "price": 10.0,
        "status": "active",
        "createdAt": "2026-08-22T10:00:00Z",
        "updatedAt": "2026-08-22T10:00:00Z",
    }


def valid_item_list() -> dict[str, JsonValue]:
    return {"items": [], "offset": 0, "limit": 20, "total": 0}


def run_scenario(
    scenario: Scenario,
    handler: httpx.MockTransport,
    *,
    variables: dict[str, JsonValue] | None = None,
):
    validator = OpenAPIContractValidator(SPEC, BASE_URL)
    with HttpTransport(BASE_URL, transport=handler) as transport:
        return execute_scenario_main(
            scenario,
            variables or {},
            SPEC,
            DEFAULTS,
            validator,
            transport,
        )


def run_flow(
    scenario: Scenario,
    handler: httpx.MockTransport,
    *,
    variables: dict[str, JsonValue] | None = None,
):
    validator = OpenAPIContractValidator(SPEC, BASE_URL)
    with HttpTransport(BASE_URL, transport=handler) as transport:
        return execute_scenario_flow(
            scenario,
            variables or {},
            SPEC,
            DEFAULTS,
            validator,
            transport,
        )


def create_step(*, expected_status: int = 201) -> dict[str, object]:
    return {
        "id": "create",
        "operation_id": "createItem",
        "request": {
            "body": {
                "name": {"$var": "initial_name"},
                "price": 10.0,
                "status": "active",
            }
        },
        "extract": [{"variable": "item_id", "source": "response.body", "pointer": "/id"}],
        "assertions": [{"operator": "status_is", "expected": expected_status}],
    }


def read_step() -> dict[str, object]:
    return {
        "id": "read",
        "operation_id": "getItem",
        "request": {"path": {"itemId": {"$var": "item_id"}}},
        "assertions": [
            {"operator": "status_is", "expected": 200},
            {
                "operator": "equals",
                "actual": {"source": "response.body", "pointer": "/name"},
                "expected": {"$var": "initial_name"},
            },
        ],
    }


def test_runs_setup_then_main_and_passes_extracted_variables_forward() -> None:
    calls: list[tuple[str, str]] = []
    initial_variables: dict[str, JsonValue] = {
        "initial_name": "Created Item",
        "item_id": 99,
    }
    scenario = Scenario.model_validate(
        {
            "id": "create-read",
            "setup": [create_step()],
            "steps": [read_step()],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(201, json=valid_item())
        return httpx.Response(200, json=valid_item())

    execution = run_scenario(
        scenario,
        httpx.MockTransport(handler),
        variables=initial_variables,
    )

    assert calls == [("POST", "/api/items"), ("GET", "/api/items/7")]
    assert [step.result.phase for step in execution.step_executions] == [
        StepPhase.SETUP,
        StepPhase.MAIN,
    ]
    assert execution.completed is True
    assert execution.halted_after_step is None
    assert execution.variables == {
        "initial_name": "Created Item",
        "item_id": 7,
    }
    assert initial_variables == {"initial_name": "Created Item", "item_id": 99}


def test_setup_failure_prevents_main_steps_from_running() -> None:
    calls = 0
    scenario = Scenario.model_validate(
        {
            "id": "blocked-by-setup",
            "setup": [
                {
                    "id": "setup-check",
                    "operation_id": "listItems",
                    "assertions": [{"operator": "status_is", "expected": 201}],
                }
            ],
            "steps": [{"id": "main-list", "operation_id": "listItems"}],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=valid_item_list())

    execution = run_scenario(scenario, httpx.MockTransport(handler))

    assert calls == 1
    assert len(execution.step_executions) == 1
    assert execution.step_executions[0].result.outcome is ExecutionOutcome.FAILED
    assert execution.halted_after_step == "setup-check"
    assert execution.completed is False


def test_failed_main_step_keeps_its_extracted_values_and_stops_following_steps() -> None:
    calls = 0
    scenario = Scenario.model_validate(
        {
            "id": "failed-create",
            "steps": [create_step(expected_status=200), read_step()],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(201, json=valid_item())

    execution = run_scenario(
        scenario,
        httpx.MockTransport(handler),
        variables={"initial_name": "Created Item"},
    )

    assert calls == 1
    assert len(execution.step_executions) == 1
    assert execution.step_executions[0].result.outcome is ExecutionOutcome.FAILED
    assert execution.variables["item_id"] == 7
    assert execution.halted_after_step == "create"


def test_success_runs_always_and_on_success_cleanup_and_skips_on_failure() -> None:
    calls = 0
    scenario = Scenario.model_validate(
        {
            "id": "successful-cleanup",
            "steps": [{"id": "main-list", "operation_id": "listItems"}],
            "cleanup": [
                {"id": "cleanup-always", "operation_id": "listItems", "when": "always"},
                {
                    "id": "cleanup-success",
                    "operation_id": "listItems",
                    "when": "on_success",
                },
                {
                    "id": "cleanup-failure",
                    "operation_id": "listItems",
                    "when": "on_failure",
                },
            ],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=valid_item_list())

    execution = run_flow(scenario, httpx.MockTransport(handler))

    assert calls == 3
    assert [step.result.outcome for step in execution.cleanup_executions] == [
        ExecutionOutcome.PASSED,
        ExecutionOutcome.PASSED,
        ExecutionOutcome.SKIPPED,
    ]
    assert [step.result.step_id for step in execution.step_executions] == [
        "main-list",
        "cleanup-always",
        "cleanup-success",
        "cleanup-failure",
    ]


def test_failure_runs_always_and_on_failure_cleanup_and_skips_on_success() -> None:
    calls = 0
    scenario = Scenario.model_validate(
        {
            "id": "failed-cleanup",
            "steps": [
                {
                    "id": "main-list",
                    "operation_id": "listItems",
                    "assertions": [{"operator": "status_is", "expected": 201}],
                }
            ],
            "cleanup": [
                {"id": "cleanup-always", "operation_id": "listItems", "when": "always"},
                {
                    "id": "cleanup-success",
                    "operation_id": "listItems",
                    "when": "on_success",
                },
                {
                    "id": "cleanup-failure",
                    "operation_id": "listItems",
                    "when": "on_failure",
                },
            ],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=valid_item_list())

    execution = run_flow(scenario, httpx.MockTransport(handler))

    assert calls == 3
    assert execution.main.completed is False
    assert [step.result.outcome for step in execution.cleanup_executions] == [
        ExecutionOutcome.PASSED,
        ExecutionOutcome.SKIPPED,
        ExecutionOutcome.PASSED,
    ]


def test_cleanup_can_use_a_value_extracted_by_a_failed_main_step() -> None:
    calls: list[tuple[str, str]] = []
    scenario = Scenario.model_validate(
        {
            "id": "cleanup-failed-create",
            "steps": [create_step(expected_status=200)],
            "cleanup": [
                {
                    "id": "delete-created",
                    "operation_id": "deleteItem",
                    "request": {"path": {"itemId": {"$var": "item_id"}}},
                }
            ],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(201, json=valid_item())
        return httpx.Response(204)

    execution = run_flow(
        scenario,
        httpx.MockTransport(handler),
        variables={"initial_name": "Created Item"},
    )

    assert calls == [("POST", "/api/items"), ("DELETE", "/api/items/7")]
    assert execution.main.completed is False
    assert execution.cleanup_executions[0].result.outcome is ExecutionOutcome.PASSED


def test_cleanup_continues_after_failure_and_records_outcome_policy() -> None:
    calls = 0
    scenario = Scenario.model_validate(
        {
            "id": "cleanup-policies",
            "steps": [{"id": "main-list", "operation_id": "listItems"}],
            "cleanup": [
                {
                    "id": "required-cleanup",
                    "operation_id": "listItems",
                    "assertions": [{"operator": "status_is", "expected": 201}],
                },
                {
                    "id": "best-effort-cleanup",
                    "operation_id": "listItems",
                    "ignore_errors": True,
                    "assertions": [{"operator": "status_is", "expected": 201}],
                },
            ],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=valid_item_list())

    execution = run_flow(scenario, httpx.MockTransport(handler))

    assert calls == 3
    assert [step.result.outcome for step in execution.cleanup_executions] == [
        ExecutionOutcome.FAILED,
        ExecutionOutcome.FAILED,
    ]
    assert [step.result.outcome_policy for step in execution.cleanup_executions] == [
        OutcomePolicy.REQUIRED,
        OutcomePolicy.BEST_EFFORT,
    ]
