from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from services.demo_items.app import app as demo_app
from services.demo_items.app import reset_state
from services.fault_proxy.app import FAULT_HEADER, create_app
from services.fault_proxy.catalog import load_fault_catalog

PROJECT_ROOT = Path(__file__).parents[2]
DEMO_FAULTS = PROJECT_ROOT / "benchmarks" / "demo_items" / "faults"


def proxy_client() -> TestClient:
    proxy_app = create_app(
        "http://demo.test",
        faults=load_fault_catalog(DEMO_FAULTS),
        transport=httpx.ASGITransport(app=demo_app),
    )
    return TestClient(proxy_app)


def create_item(client: TestClient) -> None:
    response = client.post(
        "/items",
        json={"name": "pencil", "price": 1.5, "status": "active"},
    )
    assert response.status_code == 201


def assert_triggered_once(client: TestClient, fault_id: str) -> None:
    state = client.get("/__oate__/state")
    assert state.json() == {
        "mode": "active",
        "configured_fault_id": fault_id,
        "trigger_count": 1,
    }


def test_get_status_error_is_triggerable_and_observable() -> None:
    reset_state()
    with proxy_client() as client:
        create_item(client)
        clean = client.get("/items/1")
        client.put("/__oate__/faults/get-status-error")
        faulty = client.get("/items/1")
        assert_triggered_once(client, "get-status-error")

    assert clean.status_code == 200
    assert faulty.status_code == 500
    assert faulty.headers[FAULT_HEADER] == "get-status-error"


def test_get_missing_name_is_triggerable_and_observable() -> None:
    reset_state()
    with proxy_client() as client:
        create_item(client)
        clean = client.get("/items/1")
        client.put("/__oate__/faults/get-missing-name")
        faulty = client.get("/items/1")
        assert_triggered_once(client, "get-missing-name")

    assert clean.json()["name"] == "pencil"
    assert "name" not in faulty.json()
    assert faulty.headers[FAULT_HEADER] == "get-missing-name"


def test_get_id_as_string_is_triggerable_and_observable() -> None:
    reset_state()
    with proxy_client() as client:
        create_item(client)
        clean = client.get("/items/1")
        client.put("/__oate__/faults/get-id-as-string")
        faulty = client.get("/items/1")
        assert_triggered_once(client, "get-id-as-string")

    assert clean.json()["id"] == 1
    assert faulty.json()["id"] == "invalid-id"
    assert faulty.headers[FAULT_HEADER] == "get-id-as-string"


def test_list_duplicate_item_is_triggerable_and_observable() -> None:
    reset_state()
    with proxy_client() as client:
        create_item(client)
        clean = client.get("/items")
        client.put("/__oate__/faults/list-duplicate-first-item")
        faulty = client.get("/items")
        assert_triggered_once(client, "list-duplicate-first-item")

    assert len(clean.json()["items"]) == 1
    assert len(faulty.json()["items"]) == 2
    assert faulty.json()["total"] == clean.json()["total"] == 1
    assert faulty.headers[FAULT_HEADER] == "list-duplicate-first-item"
