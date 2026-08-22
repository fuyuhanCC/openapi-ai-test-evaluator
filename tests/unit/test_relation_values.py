from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.execution import StepPhase
from openapi_ai_test_evaluator.domain.test_plan import (
    PlanDefaults,
    RelationFieldReference,
    RequestStep,
)
from openapi_ai_test_evaluator.execution import (
    REDACTED_VALUE,
    HttpTransport,
    OpenAPIContractValidator,
    RelationValueSelectionError,
    StepExecution,
    execute_step,
    select_relation_value,
)
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")
BASE_URL = "https://example.test/api"
DEFAULTS = PlanDefaults()


def valid_item(**changes: JsonValue) -> dict[str, JsonValue]:
    item: dict[str, JsonValue] = {
        "id": 7,
        "name": "Created Item",
        "price": 10.0,
        "status": "active",
        "category": None,
        "createdAt": "2026-08-22T10:00:00Z",
        "updatedAt": "2026-08-22T10:00:00Z",
    }
    item.update(changes)
    return item


def run_step(
    step: RequestStep,
    handler: httpx.MockTransport,
    *,
    variables: dict[str, JsonValue] | None = None,
) -> StepExecution:
    validator = OpenAPIContractValidator(SPEC, BASE_URL)
    with HttpTransport(BASE_URL, transport=handler) as transport:
        return execute_step(
            step,
            StepPhase.MAIN,
            SPEC,
            DEFAULTS,
            variables or {},
            validator,
            transport,
        )


def reference(location: str, pointer: str | None = None) -> RelationFieldReference:
    return RelationFieldReference.model_validate({"location": location, "pointer": pointer})


def create_execution(response_body: dict[str, JsonValue] | None = None) -> StepExecution:
    step = RequestStep.model_validate(
        {
            "id": "create",
            "operation_id": "createItem",
            "request": {"body": {"name": "Created Item", "price": 10.0, "status": "active"}},
        }
    )
    return run_step(
        step,
        httpx.MockTransport(
            lambda request: httpx.Response(201, json=response_body or valid_item())
        ),
    )


def test_selects_request_body_response_body_and_response_status() -> None:
    execution = create_execution()

    request_name = select_relation_value(
        execution,
        reference("request.body", "/name"),
    )
    response_id = select_relation_value(
        execution,
        reference("response.body", "/id"),
    )
    response_status = select_relation_value(
        execution,
        reference("response.status"),
    )

    assert request_name.raw_value == "Created Item"
    assert request_name.snapshot.value == "Created Item"
    assert response_id.raw_value == 7
    assert response_id.snapshot.step_id == "create"
    assert response_status.raw_value == 201
    assert response_status.snapshot.pointer is None


def test_distinguishes_existing_json_null_from_a_missing_pointer() -> None:
    execution = create_execution()

    selected_null = select_relation_value(
        execution,
        reference("response.body", "/category"),
    )

    assert selected_null.raw_value is None
    assert selected_null.snapshot.value is None
    with pytest.raises(RelationValueSelectionError, match="is missing"):
        select_relation_value(
            execution,
            reference("response.body", "/absent"),
        )


def test_reports_unavailable_request_or_response() -> None:
    missing_request_body = run_step(
        RequestStep(id="list", operation_id="listItems"),
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"items": [], "offset": 0, "limit": 20, "total": 0},
            )
        ),
    )
    build_failure = run_step(
        RequestStep.model_validate(
            {
                "id": "read",
                "operation_id": "getItem",
                "request": {"path": {"itemId": {"$var": "missing_id"}}},
            }
        ),
        httpx.MockTransport(lambda request: httpx.Response(500)),
    )

    with pytest.raises(RelationValueSelectionError, match="is missing"):
        select_relation_value(
            missing_request_body,
            reference("request.body", ""),
        )
    with pytest.raises(RelationValueSelectionError, match="response is unavailable"):
        select_relation_value(
            build_failure,
            reference("response.status"),
        )


def test_redacts_sensitive_selected_and_nested_values_only_in_snapshot() -> None:
    execution = create_execution(valid_item(access_token="unsafe-secret"))

    selected_token = select_relation_value(
        execution,
        reference("response.body", "/access_token"),
    )
    selected_body = select_relation_value(
        execution,
        reference("response.body", ""),
    )

    assert selected_token.raw_value == "unsafe-secret"
    assert selected_token.snapshot.value == REDACTED_VALUE
    assert isinstance(selected_body.snapshot.value, dict)
    assert selected_body.snapshot.value["access_token"] == REDACTED_VALUE
    assert "unsafe-secret" not in repr(selected_token)


def test_invalid_pointer_escape_is_a_relation_selection_error() -> None:
    with pytest.raises(RelationValueSelectionError, match="invalid JSON Pointer escape"):
        select_relation_value(
            create_execution(),
            reference("response.body", "/bad~2pointer"),
        )
