from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import httpx
import pytest
import uvicorn

from openapi_ai_test_evaluator.domain import TestCaseBatch as CaseBatch
from openapi_ai_test_evaluator.domain.execution import ExecutionOutcome, StepPhase
from openapi_ai_test_evaluator.execution import execute_test_case_batch
from openapi_ai_test_evaluator.spec import load_openapi
from services.demo_items.app import app, reset_state


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()

    deadline = monotonic() + 5
    while not server.started and thread.is_alive() and monotonic() < deadline:
        sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        raise RuntimeError("fixture API did not start within 5 seconds")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("fixture API did not stop within 5 seconds")


@pytest.fixture
def api(base_url: str) -> Iterator[httpx.Client]:
    reset_state()
    with httpx.Client(base_url=base_url) as client:
        yield client


def item_payload(
    name: str,
    *,
    price: float = 10,
    status: str = "active",
    category: str | None = "test",
) -> dict[str, object]:
    return {
        "name": name,
        "price": price,
        "status": status,
        "category": category,
    }


def test_crud_lifecycle_matches_demo_contract(api: httpx.Client) -> None:
    created = api.post("/items", json=item_payload("Original"))
    assert created.status_code == 201
    created_item = created.json()
    assert created_item["id"] == 1
    assert created_item["createdAt"] == "2026-01-01T00:00:00Z"
    assert created_item["updatedAt"] == created_item["createdAt"]

    fetched = api.get("/items/1")
    assert fetched.status_code == 200
    assert fetched.json() == created_item

    updated = api.patch("/items/1", json={"price": 15})
    assert updated.status_code == 200
    assert updated.json()["price"] == 15
    assert updated.json()["updatedAt"] == "2026-01-01T00:00:01Z"

    replaced = api.put(
        "/items/1",
        json=item_payload("Replacement", price=20, status="inactive"),
    )
    assert replaced.status_code == 200
    assert replaced.json()["name"] == "Replacement"
    assert replaced.json()["status"] == "inactive"
    assert replaced.json()["createdAt"] == created_item["createdAt"]

    assert api.delete("/items/1").status_code == 204
    missing = api.get("/items/1")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"


def test_list_filters_and_paginates_deterministically(api: httpx.Client) -> None:
    api.post("/items", json=item_payload("First", category="books"))
    api.post(
        "/items",
        json=item_payload("Second", status="inactive", category="books"),
    )
    api.post("/items", json=item_payload("Third", category="games"))

    response = api.get(
        "/items",
        params={"category": "books", "offset": 1, "limit": 1},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["offset"] == 1
    assert response.json()["limit"] == 1
    assert [item["name"] for item in response.json()["items"]] == ["Second"]

    active = api.get("/items", params={"status": "active"})
    assert active.status_code == 200
    assert active.json()["total"] == 2


def test_invalid_requests_use_the_declared_error_shape(api: httpx.Client) -> None:
    invalid_create = api.post("/items", json={"name": "", "price": -1})
    assert invalid_create.status_code == 400
    assert invalid_create.json() == {
        "code": "bad_request",
        "message": "request validation failed",
    }

    created = api.post("/items", json=item_payload("Original"))
    assert created.status_code == 201
    empty_update = api.patch("/items/1", json={})
    assert empty_update.status_code == 400
    assert api.patch("/items/1", json={"price": None}).status_code == 400
    assert api.get("/items/0").status_code == 400


def test_reset_control_clears_state_and_restarts_ids(api: httpx.Client) -> None:
    assert api.post("/items", json=item_payload("Before reset")).json()["id"] == 1
    assert api.post("/__test__/reset").status_code == 204
    assert api.get("/items").json()["total"] == 0
    assert api.post("/items", json=item_payload("After reset")).json()["id"] == 1


def test_runner_executes_lifecycle_over_real_http(api: httpx.Client, base_url: str) -> None:
    batch = CaseBatch.model_validate(
        {
            "schema_version": "1.0",
            "cases": [
                {
                    "id": "create-get-cleanup",
                    "steps": [
                        {
                            "id": "create",
                            "operation_id": "createItem",
                            "request": {"body": item_payload("Runner item")},
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
                        },
                        {
                            "id": "get",
                            "operation_id": "getItem",
                            "request": {"path": {"itemId": {"$var": "item_id"}}},
                            "assertions": [
                                {"operator": "status_is", "expected": 200},
                                {"operator": "schema_matches"},
                            ],
                        },
                    ],
                    "cleanup": [
                        {
                            "id": "delete",
                            "operation_id": "deleteItem",
                            "request": {"path": {"itemId": {"$var": "item_id"}}},
                            "when": "always",
                        }
                    ],
                }
            ],
        }
    )
    spec = load_openapi(Path("examples/demo-items/openapi.yaml"))

    result = execute_test_case_batch(
        batch,
        spec,
        base_url,
        batch_name="fixture-integration",
        run_id="run-fixture-integration",
        allow_mutations=True,
    )

    assert result.outcome is ExecutionOutcome.PASSED
    assert [step.phase for step in result.cases[0].steps] == [
        StepPhase.MAIN,
        StepPhase.MAIN,
        StepPhase.CLEANUP,
    ]
    assert all(step.outcome is ExecutionOutcome.PASSED for step in result.cases[0].steps)
    assert api.get("/items/1").status_code == 404
