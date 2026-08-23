from pathlib import Path

import httpx
import pytest

from openapi_ai_test_evaluator.domain.execution import (
    ExecutionOutcome,
    FaultTriggerStatus,
)
from openapi_ai_test_evaluator.domain.test_plan import TestPlan as PlanModel
from openapi_ai_test_evaluator.execution import (
    MutationExecutionRejected,
    PlanExecutionRejected,
    execute_test_plan,
    validate_base_url,
)
from openapi_ai_test_evaluator.spec import load_openapi
from openapi_ai_test_evaluator.validation import load_test_plan

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")
BASE_URL = "https://example.test/api"


def item_list() -> dict[str, object]:
    return {"items": [], "offset": 0, "limit": 20, "total": 0}


def test_executes_a_complete_plan_into_one_run_result() -> None:
    plan = load_test_plan(ROOT / "examples" / "plans" / "minimal-get.yaml")

    result = execute_test_plan(
        plan,
        SPEC,
        BASE_URL,
        run_id="run-test",
        httpx_transport=httpx.MockTransport(lambda request: httpx.Response(200, json=item_list())),
    )

    assert result.kind == "RunResult"
    assert result.run_id == "run-test"
    assert result.plan_name == "minimal-get"
    assert result.spec_id == "demo-items-v1"
    assert result.outcome is ExecutionOutcome.PASSED
    assert result.fault.trigger_status is FaultTriggerStatus.NOT_CONFIGURED
    assert result.started_at.tzinfo is not None
    assert result.finished_at >= result.started_at
    assert result.scenarios[0].steps[0].assertions[1].outcome is ExecutionOutcome.PASSED


def test_continues_with_later_scenarios_after_an_earlier_failure() -> None:
    plan = PlanModel.model_validate(
        {
            "schema_version": "1.0",
            "kind": "TestPlan",
            "metadata": {"name": "two-scenarios", "generator": {"type": "manual"}},
            "target": {"spec_id": "demo-items-v1"},
            "scenarios": [
                {
                    "id": "failed",
                    "steps": [
                        {
                            "id": "first-list",
                            "operation_id": "listItems",
                            "assertions": [
                                {
                                    "operator": "length_is",
                                    "actual": {
                                        "source": "response.body",
                                        "pointer": "/items",
                                    },
                                    "expected": 1,
                                }
                            ],
                        }
                    ],
                },
                {
                    "id": "passed",
                    "steps": [
                        {
                            "id": "second-list",
                            "operation_id": "listItems",
                            "assertions": [{"operator": "status_is", "expected": 200}],
                        }
                    ],
                },
            ],
        }
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=item_list())

    result = execute_test_plan(
        plan,
        SPEC,
        BASE_URL,
        run_id="run-two",
        httpx_transport=httpx.MockTransport(handler),
    )

    assert calls == 2
    assert result.outcome is ExecutionOutcome.FAILED
    assert [scenario.outcome for scenario in result.scenarios] == [
        ExecutionOutcome.FAILED,
        ExecutionOutcome.PASSED,
    ]


def test_rejects_semantically_invalid_plan_before_transport() -> None:
    plan = PlanModel.model_validate(
        {
            "schema_version": "1.0",
            "kind": "TestPlan",
            "metadata": {"name": "invalid-plan", "generator": {"type": "manual"}},
            "target": {"spec_id": "demo-items-v1"},
            "scenarios": [
                {
                    "id": "invalid",
                    "steps": [{"id": "request", "operation_id": "missingOperation"}],
                }
            ],
        }
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=item_list())

    with pytest.raises(PlanExecutionRejected) as raised:
        execute_test_plan(
            plan,
            SPEC,
            BASE_URL,
            httpx_transport=httpx.MockTransport(handler),
        )

    assert calls == 0
    assert raised.value.issues[0].code == "unknown_operation"


def test_rejects_mutating_plan_without_explicit_authorization() -> None:
    plan = PlanModel.model_validate(
        {
            "schema_version": "1.0",
            "kind": "TestPlan",
            "metadata": {"name": "mutating-plan", "generator": {"type": "manual"}},
            "target": {"spec_id": "demo-items-v1"},
            "scenarios": [
                {
                    "id": "create",
                    "steps": [
                        {
                            "id": "create-item",
                            "operation_id": "createItem",
                            "request": {
                                "body": {
                                    "name": "Created Item",
                                    "price": 10.0,
                                    "status": "active",
                                }
                            },
                        }
                    ],
                }
            ],
        }
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with pytest.raises(MutationExecutionRejected, match="createItem"):
        execute_test_plan(
            plan,
            SPEC,
            BASE_URL,
            httpx_transport=httpx.MockTransport(handler),
        )

    assert calls == 0


@pytest.mark.parametrize(
    ("base_url", "message"),
    [
        ("example.test/api", "absolute"),
        ("ftp://example.test/api", "absolute"),
        ("https://user:secret@example.test/api", "credentials"),
        ("https://example.test/api?unsafe=true", "query string"),
        ("https://example.test/api#fragment", "fragment"),
    ],
)
def test_rejects_unsafe_or_ambiguous_base_urls(base_url: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_base_url(base_url)


def test_normalizes_an_explicit_http_base_url() -> None:
    assert validate_base_url("http://127.0.0.1:8000/api/") == "http://127.0.0.1:8000/api"
