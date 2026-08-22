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
from openapi_ai_test_evaluator.domain.test_plan import PlanDefaults, Scenario
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


def item(
    item_id: int = 7,
    *,
    name: str = "Created Item",
    price: float = 10.0,
    **changes: JsonValue,
) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {
        "id": item_id,
        "name": name,
        "price": price,
        "status": "active",
        "createdAt": "2026-08-23T09:00:00Z",
        "updatedAt": "2026-08-23T09:00:00Z",
    }
    value.update(changes)
    return value


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


def create_read_scenario(*, follow_pointer: str = "/name") -> Scenario:
    return Scenario.model_validate(
        {
            "id": "create-read",
            "steps": [
                {
                    "id": "create",
                    "operation_id": "createItem",
                    "request": {
                        "body": {
                            "name": "Created Item",
                            "price": 10.0,
                            "status": "active",
                        }
                    },
                    "extract": [
                        {
                            "variable": "item_id",
                            "source": "response.body",
                            "pointer": "/id",
                        }
                    ],
                    "assertions": [{"operator": "status_is", "expected": 201}],
                },
                {
                    "id": "read-created",
                    "operation_id": "getItem",
                    "request": {"path": {"itemId": {"$var": "item_id"}}},
                },
            ],
            "relations": [
                {
                    "id": "created-fields-readable",
                    "type": "create_read_consistency",
                    "source_step": "create",
                    "follow_up_step": "read-created",
                    "field_pairs": [
                        {
                            "source": {
                                "location": "request.body",
                                "pointer": "/name",
                            },
                            "follow_up": {
                                "location": "response.body",
                                "pointer": follow_pointer,
                            },
                        },
                        {
                            "source": {
                                "location": "request.body",
                                "pointer": "/price",
                            },
                            "follow_up": {
                                "location": "response.body",
                                "pointer": "/price",
                            },
                        },
                    ],
                }
            ],
        }
    )


def update_read_scenario(*, follow_item_id: object = None) -> Scenario:
    follow_path_value = {"$var": "item_id"} if follow_item_id is None else follow_item_id
    return Scenario.model_validate(
        {
            "id": "update-read",
            "steps": [
                {
                    "id": "create",
                    "operation_id": "createItem",
                    "request": {
                        "body": {
                            "name": "Before Update",
                            "price": 10.0,
                            "status": "active",
                        }
                    },
                    "extract": [
                        {
                            "variable": "item_id",
                            "source": "response.body",
                            "pointer": "/id",
                        }
                    ],
                },
                {
                    "id": "update",
                    "operation_id": "updateItem",
                    "request": {
                        "path": {"itemId": {"$var": "item_id"}},
                        "body": {"name": "After Update"},
                    },
                },
                {
                    "id": "read-updated",
                    "operation_id": "getItem",
                    "request": {"path": {"itemId": follow_path_value}},
                },
            ],
            "relations": [
                {
                    "id": "updated-fields-visible",
                    "type": "update_read_consistency",
                    "baseline_step": "create",
                    "source_step": "update",
                    "follow_up_step": "read-updated",
                    "field_pairs": [
                        {
                            "source": {
                                "location": "request.body",
                                "pointer": "/name",
                            },
                            "follow_up": {
                                "location": "response.body",
                                "pointer": "/name",
                            },
                        }
                    ],
                    "stable_follow_up_pointers": ["/id"],
                }
            ],
        }
    )


def delete_read_scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "delete-read",
            "setup": [
                {
                    "id": "create",
                    "operation_id": "createItem",
                    "request": {
                        "body": {
                            "name": "Delete Me",
                            "price": 10.0,
                            "status": "active",
                        }
                    },
                    "extract": [
                        {
                            "variable": "item_id",
                            "source": "response.body",
                            "pointer": "/id",
                        }
                    ],
                }
            ],
            "steps": [
                {
                    "id": "delete",
                    "operation_id": "deleteItem",
                    "request": {"path": {"itemId": {"$var": "item_id"}}},
                    "assertions": [{"operator": "status_is", "expected": 204}],
                },
                {
                    "id": "read-deleted",
                    "operation_id": "getItem",
                    "request": {"path": {"itemId": {"$var": "item_id"}}},
                },
            ],
            "relations": [
                {
                    "id": "deleted-item-unavailable",
                    "type": "delete_read_consistency",
                    "source_step": "delete",
                    "follow_up_step": "read-deleted",
                    "accepted_follow_up_statuses": [404],
                }
            ],
        }
    )


@pytest.mark.parametrize(
    ("read_name", "expected"),
    [
        ("Created Item", RelationOutcome.PASSED),
        ("Server Changed It", RelationOutcome.FAILED),
    ],
)
def test_create_read_compares_declared_request_and_response_fields(
    read_name: str,
    expected: RelationOutcome,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json=item())
        return httpx.Response(200, json=item(name=read_name))

    execution = run_flow(create_read_scenario(), httpx.MockTransport(handler))

    result = execution.relation_results[0]
    assert result.outcome is expected
    assert [comparison.operator for comparison in result.comparisons] == [
        ComparisonOperator.EQUALS,
        ComparisonOperator.EQUALS,
    ]
    if expected is RelationOutcome.FAILED:
        assert result.errors[0].category is ErrorCategory.LIFECYCLE_CONSISTENCY_VIOLATED


@pytest.mark.parametrize(
    ("read_item", "expected"),
    [
        (item(name="After Update"), RelationOutcome.PASSED),
        (item(8, name="Before Update"), RelationOutcome.FAILED),
    ],
)
def test_update_read_checks_updated_and_stable_fields(
    read_item: dict[str, JsonValue],
    expected: RelationOutcome,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json=item(name="Before Update"))
        if request.method == "PATCH":
            return httpx.Response(200, json=item(name="After Update"))
        return httpx.Response(200, json=read_item)

    execution = run_flow(update_read_scenario(), httpx.MockTransport(handler))

    result = execution.relation_results[0]
    assert result.outcome is expected
    assert result.baseline_step == "create"
    assert [comparison.operator for comparison in result.comparisons] == [
        ComparisonOperator.UNCHANGED,
        ComparisonOperator.EQUALS,
    ]


@pytest.mark.parametrize(
    ("follow_status", "expected"),
    [(404, RelationOutcome.PASSED), (200, RelationOutcome.FAILED)],
)
def test_delete_read_checks_follow_up_status_allowlist(
    follow_status: int,
    expected: RelationOutcome,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json=item(name="Delete Me"))
        if request.method == "DELETE":
            return httpx.Response(204)
        if follow_status == 200:
            return httpx.Response(200, json=item(name="Delete Me"))
        return httpx.Response(
            follow_status,
            json={"code": "not_found", "message": "item not found"},
        )

    execution = run_flow(delete_read_scenario(), httpx.MockTransport(handler))

    result = execution.relation_results[0]
    comparison = result.comparisons[0]
    assert result.outcome is expected
    assert comparison.operator is ComparisonOperator.ONE_OF
    assert comparison.expected == [404]
    assert comparison.source.value == 204
    assert comparison.follow_up.value == follow_status


def test_update_read_is_not_applicable_for_different_resolved_resources() -> None:
    execution = run_flow(
        update_read_scenario(follow_item_id=8),
        httpx.MockTransport(
            lambda request: httpx.Response(
                201 if request.method == "POST" else 200,
                json=item(name="After Update"),
            )
        ),
    )

    result = execution.relation_results[0]
    assert result.outcome is RelationOutcome.NOT_APPLICABLE
    assert result.message == "resolved source and follow-up paths identify different resources"


def test_missing_lifecycle_field_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json=item())
        return httpx.Response(200, json=item())

    execution = run_flow(
        create_read_scenario(follow_pointer="/category"),
        httpx.MockTransport(handler),
    )

    result = execution.relation_results[0]
    assert result.outcome is RelationOutcome.ERROR
    assert "is missing" in (result.message or "")
    assert result.errors[0].category is ErrorCategory.LIFECYCLE_CONSISTENCY_VIOLATED


def test_lifecycle_relation_is_not_applicable_when_follow_up_did_not_execute() -> None:
    scenario = create_read_scenario()
    scenario.steps[0].assertions[0].expected = 200

    execution = run_flow(
        scenario,
        httpx.MockTransport(lambda request: httpx.Response(201, json=item())),
    )

    result = execution.relation_results[0]
    assert len(execution.main.step_executions) == 1
    assert execution.main.step_executions[0].result.outcome is ExecutionOutcome.FAILED
    assert result.outcome is RelationOutcome.NOT_APPLICABLE
    assert result.message == "referenced steps did not both execute"


def test_relation_dispatcher_preserves_mixed_declaration_order() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "mixed-relations",
            "steps": [
                {
                    "id": "create",
                    "operation_id": "createItem",
                    "request": {
                        "body": {
                            "name": "Created Item",
                            "price": 10.0,
                            "status": "active",
                        }
                    },
                    "extract": [
                        {
                            "variable": "item_id",
                            "source": "response.body",
                            "pointer": "/id",
                        }
                    ],
                },
                {
                    "id": "first-read",
                    "operation_id": "getItem",
                    "request": {"path": {"itemId": {"$var": "item_id"}}},
                },
                {
                    "id": "second-read",
                    "operation_id": "getItem",
                    "request": {"path": {"itemId": {"$var": "item_id"}}},
                },
            ],
            "relations": [
                {
                    "id": "repeat-first",
                    "type": "repeated_read_consistency",
                    "source_step": "first-read",
                    "follow_up_step": "second-read",
                    "compare_pointers": ["/id"],
                },
                {
                    "id": "create-second",
                    "type": "create_read_consistency",
                    "source_step": "create",
                    "follow_up_step": "first-read",
                    "field_pairs": [
                        {
                            "source": {
                                "location": "request.body",
                                "pointer": "/name",
                            },
                            "follow_up": {
                                "location": "response.body",
                                "pointer": "/name",
                            },
                        }
                    ],
                },
            ],
        }
    )

    execution = run_flow(
        scenario,
        httpx.MockTransport(
            lambda request: httpx.Response(
                201 if request.method == "POST" else 200,
                json=item(),
            )
        ),
    )

    assert [result.relation_id for result in execution.relation_results] == [
        "repeat-first",
        "create-second",
    ]
    assert all(result.outcome is RelationOutcome.PASSED for result in execution.relation_results)
