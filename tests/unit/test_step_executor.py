from pathlib import Path

import httpx

from openapi_ai_test_evaluator.domain.execution import (
    ErrorCategory,
    ExecutionOutcome,
    ExtractionStatus,
    StepPhase,
)
from openapi_ai_test_evaluator.domain.test_plan import PlanDefaults, RequestStep
from openapi_ai_test_evaluator.execution import (
    HttpTransport,
    OpenAPIContractValidator,
    execute_step,
)
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")
BASE_URL = "https://example.test/api"
DEFAULTS = PlanDefaults()


def valid_item() -> dict[str, object]:
    return {
        "id": 1,
        "name": "Created Item",
        "price": 10.0,
        "status": "active",
        "createdAt": "2026-08-22T10:00:00Z",
        "updatedAt": "2026-08-22T10:00:00Z",
    }


def run_step(
    step: RequestStep,
    handler: httpx.MockTransport,
    *,
    variables: dict[str, object] | None = None,
):
    runtime_variables = variables or {}
    validator = OpenAPIContractValidator(SPEC, BASE_URL)
    with HttpTransport(BASE_URL, transport=handler) as transport:
        return execute_step(
            step,
            StepPhase.MAIN,
            SPEC,
            DEFAULTS,
            runtime_variables,  # type: ignore[arg-type]
            validator,
            transport,
        )


def create_step(**changes: object) -> RequestStep:
    values: dict[str, object] = {
        "id": "create",
        "operation_id": "createItem",
        "request": {"body": {"name": "Created Item", "price": 10.0, "status": "active"}},
        "extract": [
            {
                "variable": "item_id",
                "source": "response.body",
                "pointer": "/id",
            }
        ],
        "assertions": [
            {"operator": "status_is", "expected": 201},
            {"operator": "schema_matches"},
        ],
    }
    values.update(changes)
    return RequestStep.model_validate(values)


def test_executes_the_complete_successful_step_pipeline() -> None:
    variables: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/items"
        return httpx.Response(201, json=valid_item())

    execution = run_step(
        create_step(),
        httpx.MockTransport(handler),
        variables=variables,
    )

    assert execution.result.outcome is ExecutionOutcome.PASSED
    assert execution.result.request is not None
    assert execution.result.response is not None
    assert execution.result.response.status_code == 201
    assert execution.result.extractions[0].status is ExtractionStatus.EXTRACTED
    assert execution.extracted_values == (("item_id", 1),)
    assert execution.prepared_request is not None
    assert execution.processed_response is not None
    assert variables == {}


def test_maps_a_failed_status_assertion_to_a_step_failure() -> None:
    step = create_step(
        assertions=[{"operator": "status_is", "expected": 200}],
    )

    execution = run_step(
        step,
        httpx.MockTransport(lambda request: httpx.Response(201, json=valid_item())),
    )

    assert execution.result.outcome is ExecutionOutcome.FAILED
    assert execution.result.errors[0].category is ErrorCategory.UNEXPECTED_STATUS
    assert execution.result.errors[0].assertion_id == "assertion-1"


def test_required_missing_extraction_fails_the_step() -> None:
    step = create_step(
        extract=[
            {
                "variable": "missing_value",
                "source": "response.body",
                "pointer": "/absent",
            }
        ]
    )

    execution = run_step(
        step,
        httpx.MockTransport(lambda request: httpx.Response(201, json=valid_item())),
    )

    assert execution.result.outcome is ExecutionOutcome.FAILED
    assert execution.result.extractions[0].status is ExtractionStatus.MISSING
    assert execution.result.errors[0].category is ErrorCategory.EXTRACTION_FAILED
    assert execution.extracted_values == ()


def test_blocks_an_accidentally_invalid_conformant_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(201, json=valid_item())

    step = create_step(
        request={
            "body": {
                "name": {"$var": "runtime_name"},
                "price": 10.0,
                "status": "active",
            }
        }
    )

    execution = run_step(
        step,
        httpx.MockTransport(handler),
        variables={"runtime_name": 7},
    )

    assert calls == 0
    assert execution.result.outcome is ExecutionOutcome.ERROR
    assert execution.result.request is not None
    assert execution.result.response is None
    assert execution.result.errors[0].category is ErrorCategory.REQUEST_BUILD_FAILED
    assert "conformant request violates" in execution.result.errors[0].message


def test_sends_an_intentionally_invalid_request_that_has_contract_issues() -> None:
    calls = 0
    step = create_step(
        request={
            "mode": "intentionally_invalid",
            "expected_violations": [
                {"code": "missing_required", "location": "body", "field": "name"}
            ],
            "body": {"price": 10.0, "status": "active"},
        },
        extract=[],
        assertions=[
            {"operator": "status_is", "expected": 400},
            {"operator": "schema_matches"},
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"code": "bad_request", "message": "name required"})

    execution = run_step(step, httpx.MockTransport(handler))

    assert calls == 1
    assert execution.result.outcome is ExecutionOutcome.PASSED
    assert execution.result.errors == []


def test_maps_transport_failure_without_creating_a_response_snapshot() -> None:
    step = RequestStep(id="list", operation_id="listItems")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    execution = run_step(step, httpx.MockTransport(handler))

    assert execution.result.outcome is ExecutionOutcome.ERROR
    assert execution.result.request is not None
    assert execution.result.response is None
    assert execution.result.errors[0].category is ErrorCategory.TIMEOUT
    assert execution.processed_response is None


def test_maps_request_build_failure_without_sending() -> None:
    calls = 0
    step = RequestStep.model_validate(
        {
            "id": "read",
            "operation_id": "getItem",
            "request": {"path": {"itemId": {"$var": "missing_id"}}},
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=valid_item())

    execution = run_step(step, httpx.MockTransport(handler))

    assert calls == 0
    assert execution.result.outcome is ExecutionOutcome.ERROR
    assert execution.result.request is None
    assert execution.result.errors[0].category is ErrorCategory.REQUEST_BUILD_FAILED
