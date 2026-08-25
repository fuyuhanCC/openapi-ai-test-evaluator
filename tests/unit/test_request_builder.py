from copy import deepcopy
from pathlib import Path

import pytest

from openapi_ai_test_evaluator.domain.test_plan import QueryParameter, RequestStep
from openapi_ai_test_evaluator.execution import RequestBuildError, build_request
from openapi_ai_test_evaluator.spec import load_openapi
from openapi_ai_test_evaluator.validation import load_test_plan

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")
ALL_METHODS_PLAN = load_test_plan(ROOT / "examples" / "plans" / "all-methods.yaml")


def plan_step(step_id: str) -> RequestStep:
    for step in ALL_METHODS_PLAN.scenarios[0].steps:
        if step.id == step_id:
            return step
    raise AssertionError(f"missing example step {step_id}")


def test_builds_request_from_operation_and_plan_values() -> None:
    request = build_request(
        plan_step("create"),
        SPEC,
        variables={"initial_name": "Test Item"},
        defaults=ALL_METHODS_PLAN.defaults,
    )

    assert request.operation_id == "createItem"
    assert request.method == "POST"
    assert request.path == "/items"
    assert request.path_parameters == ()
    assert request.query == ()
    assert request.has_json_body is True
    assert request.json_body == {
        "name": "Test Item",
        "price": 10.0,
        "status": "active",
    }
    assert request.timeout_ms == 5000


def test_distinguishes_absent_body_from_explicit_json_null() -> None:
    absent = build_request(
        RequestStep(id="list", operation_id="listItems"),
        SPEC,
        variables={},
        defaults=ALL_METHODS_PLAN.defaults,
    )
    explicit_null = build_request(
        RequestStep.model_validate(
            {
                "id": "create",
                "operation_id": "createItem",
                "request": {"body": None},
            }
        ),
        SPEC,
        variables={},
        defaults=ALL_METHODS_PLAN.defaults,
    )

    assert absent.has_json_body is False
    assert absent.json_body is None
    assert explicit_null.has_json_body is True
    assert explicit_null.json_body is None


def test_resolves_and_url_encodes_path_variable() -> None:
    request = build_request(
        plan_step("read"),
        SPEC,
        variables={"item_id": "folder/item 1"},
        defaults=ALL_METHODS_PLAN.defaults,
    )

    assert request.path == "/items/folder%2Fitem%201"
    assert request.path_parameters == (("itemId", "folder/item 1"),)


def test_preserves_query_parameter_order_and_duplicates() -> None:
    step = RequestStep(
        id="list",
        operation_id="listItems",
        request={
            "query": [
                QueryParameter(name="status", value="active"),
                QueryParameter(name="category", value="book"),
                QueryParameter(name="category", value="archive"),
            ]
        },
    )

    request = build_request(step, SPEC, variables={}, defaults=ALL_METHODS_PLAN.defaults)

    assert request.query == (
        ("status", "active"),
        ("category", "book"),
        ("category", "archive"),
    )


def test_step_headers_override_defaults_case_insensitively() -> None:
    defaults = ALL_METHODS_PLAN.defaults.model_copy(
        update={"headers": {"Authorization": "default-token", "Accept": "application/json"}}
    )
    step = RequestStep(
        id="list",
        operation_id="listItems",
        request={"headers": {"authorization": "step-token"}},
    )

    request = build_request(step, SPEC, variables={}, defaults=defaults)

    assert request.headers == {
        "authorization": "step-token",
        "Accept": "application/json",
    }


def test_resolves_nested_body_variables_without_mutating_step() -> None:
    step = plan_step("create")
    original_body = deepcopy(step.request.body)

    request = build_request(
        step,
        SPEC,
        variables={"initial_name": "Resolved Name"},
        defaults=ALL_METHODS_PLAN.defaults,
    )

    assert request.json_body == {
        "name": "Resolved Name",
        "price": 10.0,
        "status": "active",
    }
    assert step.request.body == original_body


def test_reports_unknown_runtime_variable() -> None:
    with pytest.raises(RequestBuildError, match="unknown runtime variable 'item_id'") as caught:
        build_request(plan_step("read"), SPEC, variables={}, defaults=ALL_METHODS_PLAN.defaults)

    assert caught.value.location == "request.path.itemId"


def test_rejects_composite_parameter_serialization() -> None:
    step = RequestStep(
        id="list",
        operation_id="listItems",
        request={"query": [{"name": "category", "value": ["book", "archive"]}]},
    )

    with pytest.raises(RequestBuildError, match="outside the V1 runtime subset"):
        build_request(step, SPEC, variables={}, defaults=ALL_METHODS_PLAN.defaults)


def test_reports_missing_and_unknown_path_parameters() -> None:
    missing = RequestStep(id="read", operation_id="getItem")
    with pytest.raises(RequestBuildError, match="missing path parameters: itemId"):
        build_request(missing, SPEC, variables={}, defaults=ALL_METHODS_PLAN.defaults)

    extra = RequestStep(
        id="list",
        operation_id="listItems",
        request={"path": {"unexpected": 1}},
    )
    with pytest.raises(RequestBuildError, match="unknown path parameters: unexpected"):
        build_request(extra, SPEC, variables={}, defaults=ALL_METHODS_PLAN.defaults)
