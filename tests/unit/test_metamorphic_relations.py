from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.execution import (
    ComparisonOperator,
    ErrorCategory,
    ExecutionOutcome,
    RelationOutcome,
)
from openapi_ai_test_evaluator.domain.test_plan import Assertion, PlanDefaults, Scenario
from openapi_ai_test_evaluator.execution import (
    HttpTransport,
    OpenAPIContractValidator,
    execute_scenario_flow,
)
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")
BASE_URL = "https://example.test/api"
DEFAULTS = PlanDefaults()


def item(item_id: int, *, name: str | None = None, updated_at: str = "2026-08-22T10:00:00Z"):
    return {
        "id": item_id,
        "name": name or f"Item {item_id}",
        "price": 10.0,
        "status": "active",
        "createdAt": "2026-08-22T09:00:00Z",
        "updatedAt": updated_at,
    }


def item_list(items: list[dict[str, JsonValue]], *, limit: int = 20):
    return {"items": items, "offset": 0, "limit": limit, "total": len(items)}


def run_flow(scenario: Scenario, handler: httpx.MockTransport):
    validator = OpenAPIContractValidator(SPEC, BASE_URL)
    with HttpTransport(BASE_URL, transport=handler) as transport:
        return execute_scenario_flow(
            scenario,
            {},
            SPEC,
            DEFAULTS,
            validator,
            transport,
        )


def repeated_read_scenario(*, cleanup: bool = False) -> Scenario:
    raw: dict[str, object] = {
        "id": "repeated-read",
        "steps": [
            {
                "id": "first-read",
                "operation_id": "getItem",
                "request": {"path": {"itemId": 1}},
            },
            {
                "id": "second-read",
                "operation_id": "getItem",
                "request": {"path": {"itemId": 1}},
            },
        ],
        "relations": [
            {
                "id": "stable-item",
                "type": "repeated_read_consistency",
                "source_step": "first-read",
                "follow_up_step": "second-read",
                "compare_pointers": [""],
                "ignore_pointers": ["/updatedAt"],
            }
        ],
    }
    if cleanup:
        raw["cleanup"] = [
            {
                "id": "delete-item",
                "operation_id": "deleteItem",
                "request": {"path": {"itemId": 1}},
            }
        ]
    return Scenario.model_validate(raw)


def query_order_scenario(*, same_order: bool = False) -> Scenario:
    follow_query = (
        [
            {"name": "status", "value": "active"},
            {"name": "category", "value": "book"},
        ]
        if same_order
        else [
            {"name": "category", "value": "book"},
            {"name": "status", "value": "active"},
        ]
    )
    return Scenario.model_validate(
        {
            "id": "query-order",
            "steps": [
                {
                    "id": "first-query",
                    "operation_id": "listItems",
                    "request": {
                        "query": [
                            {"name": "status", "value": "active"},
                            {"name": "category", "value": "book"},
                        ]
                    },
                },
                {
                    "id": "second-query",
                    "operation_id": "listItems",
                    "request": {"query": follow_query},
                },
            ],
            "relations": [
                {
                    "id": "same-item-set",
                    "type": "query_parameter_order_invariance",
                    "source_step": "first-query",
                    "follow_up_step": "second-query",
                    "collection_pointer": "/items",
                    "item_key_pointer": "/id",
                }
            ],
        }
    )


def pagination_scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "pagination",
            "steps": [
                {
                    "id": "small-page",
                    "operation_id": "listItems",
                    "request": {
                        "query": [
                            {"name": "offset", "value": 0},
                            {"name": "limit", "value": 2},
                        ]
                    },
                },
                {
                    "id": "large-page",
                    "operation_id": "listItems",
                    "request": {
                        "query": [
                            {"name": "offset", "value": 0},
                            {"name": "limit", "value": 3},
                        ]
                    },
                },
            ],
            "relations": [
                {
                    "id": "small-is-subset",
                    "type": "pagination_monotonicity",
                    "source_step": "small-page",
                    "follow_up_step": "large-page",
                    "collection_pointer": "/items",
                    "item_key_pointer": "/id",
                    "mode": "subset",
                    "page_size_parameter": "limit",
                },
                {
                    "id": "small-is-prefix",
                    "type": "pagination_monotonicity",
                    "source_step": "small-page",
                    "follow_up_step": "large-page",
                    "collection_pointer": "/items",
                    "item_key_pointer": "/id",
                    "mode": "prefix",
                    "page_size_parameter": "limit",
                },
            ],
        }
    )


def test_repeated_read_ignores_declared_nested_changes() -> None:
    responses = iter(
        [
            item(1, updated_at="2026-08-22T10:00:00Z"),
            item(1, updated_at="2026-08-22T10:01:00Z"),
        ]
    )

    execution = run_flow(
        repeated_read_scenario(),
        httpx.MockTransport(lambda request: httpx.Response(200, json=next(responses))),
    )

    result = execution.metamorphic_results[0]
    assert result.outcome is RelationOutcome.PASSED
    assert result.comparisons[0].operator is ComparisonOperator.EQUALS


def test_repeated_read_failure_is_structured_and_cleanup_still_runs() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json=item(1, name=f"version-{calls}"))

    execution = run_flow(
        repeated_read_scenario(cleanup=True),
        httpx.MockTransport(handler),
    )

    result = execution.metamorphic_results[0]
    assert calls == 3
    assert result.outcome is RelationOutcome.FAILED
    assert result.comparisons[0].outcome is ExecutionOutcome.FAILED
    assert result.errors[0].category is ErrorCategory.METAMORPHIC_RELATION_VIOLATED
    assert execution.cleanup_executions[0].result.outcome is ExecutionOutcome.PASSED


@pytest.mark.parametrize(
    ("second_ids", "expected"),
    [([2, 1], RelationOutcome.PASSED), ([1, 3], RelationOutcome.FAILED)],
)
def test_query_order_compares_item_key_sets(
    second_ids: list[int],
    expected: RelationOutcome,
) -> None:
    responses = iter(
        [
            item_list([item(1, name="first-a"), item(2, name="first-b")]),
            item_list([item(value, name=f"second-{value}") for value in second_ids]),
        ]
    )

    execution = run_flow(
        query_order_scenario(),
        httpx.MockTransport(lambda request: httpx.Response(200, json=next(responses))),
    )

    result = execution.metamorphic_results[0]
    assert result.outcome is expected
    assert result.comparisons[0].operator is ComparisonOperator.SET_EQUALS


def test_query_order_is_not_applicable_when_runtime_order_did_not_change() -> None:
    execution = run_flow(
        query_order_scenario(same_order=True),
        httpx.MockTransport(lambda request: httpx.Response(200, json=item_list([item(1)]))),
    )

    result = execution.metamorphic_results[0]
    assert result.outcome is RelationOutcome.NOT_APPLICABLE
    assert result.message == "resolved query parameter order did not change"
    assert result.comparisons == []


def test_pagination_supports_subset_and_prefix_modes() -> None:
    responses = iter(
        [
            item_list([item(1), item(3)], limit=2),
            item_list([item(3), item(2), item(1)], limit=3),
        ]
    )

    execution = run_flow(
        pagination_scenario(),
        httpx.MockTransport(lambda request: httpx.Response(200, json=next(responses))),
    )

    subset, prefix = execution.metamorphic_results
    assert subset.outcome is RelationOutcome.PASSED
    assert subset.comparisons[0].operator is ComparisonOperator.SUBSET
    assert prefix.outcome is RelationOutcome.FAILED
    assert prefix.comparisons[0].operator is ComparisonOperator.PREFIX


def test_invalid_relation_response_shape_is_an_error() -> None:
    responses = iter(
        [
            item_list([item(1)]),
            {"items": {"id": 1}, "offset": 0, "limit": 20, "total": 1},
        ]
    )

    execution = run_flow(
        query_order_scenario(),
        httpx.MockTransport(lambda request: httpx.Response(200, json=next(responses))),
    )

    result = execution.metamorphic_results[0]
    assert result.outcome is RelationOutcome.ERROR
    assert "is not an array" in (result.message or "")
    assert result.errors[0].category is ErrorCategory.METAMORPHIC_RELATION_VIOLATED


def test_relation_is_not_applicable_when_follow_up_step_did_not_execute() -> None:
    scenario = query_order_scenario()
    scenario.steps[0].assertions.append(
        Assertion.model_validate({"operator": "status_is", "expected": 201})
    )

    execution = run_flow(
        scenario,
        httpx.MockTransport(lambda request: httpx.Response(200, json=item_list([item(1)]))),
    )

    result = execution.metamorphic_results[0]
    assert len(execution.main.step_executions) == 1
    assert result.outcome is RelationOutcome.NOT_APPLICABLE
    assert result.message == "referenced steps did not both execute"
