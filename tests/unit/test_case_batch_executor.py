from pathlib import Path

import httpx
import pytest

from openapi_ai_test_evaluator.domain import TestCaseBatch as CaseBatchModel
from openapi_ai_test_evaluator.domain.execution import ExecutionOutcome
from openapi_ai_test_evaluator.execution import (
    CaseBatchExecutionRejected,
    MutationExecutionRejected,
    execute_test_case_batch,
)
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")
BASE_URL = "https://example.test/api"


def batch_with_step(step: dict[str, object]) -> CaseBatchModel:
    return CaseBatchModel.model_validate(
        {
            "schema_version": "1.0",
            "cases": [{"id": "generated-case", "steps": [step]}],
        }
    )


def test_executes_a_case_batch_without_a_test_plan_input() -> None:
    batch = batch_with_step(
        {
            "id": "list",
            "operation_id": "listItems",
            "assertions": [{"operator": "status_is", "expected": 200}],
        }
    )

    result = execute_test_case_batch(
        batch,
        SPEC,
        BASE_URL,
        batch_name="model-output",
        run_id="run-case-batch",
        httpx_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"items": [], "offset": 0, "limit": 20, "total": 0},
            )
        ),
    )

    assert result.run_id == "run-case-batch"
    assert result.plan_name == "model-output"
    assert result.outcome is ExecutionOutcome.PASSED
    assert result.scenarios[0].scenario_id == "generated-case"


def test_validates_cases_against_openapi_before_transport() -> None:
    batch = batch_with_step({"id": "request", "operation_id": "missingOperation"})
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    with pytest.raises(CaseBatchExecutionRejected) as raised:
        execute_test_case_batch(
            batch,
            SPEC,
            BASE_URL,
            httpx_transport=httpx.MockTransport(handler),
        )

    assert calls == 0
    assert raised.value.issues[0].code == "unknown_operation"
    assert raised.value.issues[0].path == "cases[0].steps[0].operation_id"


def test_preserves_mutation_authorization_for_generated_cases() -> None:
    batch = batch_with_step(
        {
            "id": "create",
            "operation_id": "createItem",
            "request": {"body": {"name": "Generated", "price": 10.0, "status": "active"}},
        }
    )

    with pytest.raises(MutationExecutionRejected):
        execute_test_case_batch(
            batch,
            SPEC,
            BASE_URL,
            httpx_transport=httpx.MockTransport(lambda request: httpx.Response(201)),
        )
