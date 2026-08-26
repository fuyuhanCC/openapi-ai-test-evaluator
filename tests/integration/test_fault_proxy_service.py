import json

import httpx
from fastapi.testclient import TestClient

from openapi_ai_test_evaluator.domain.fault import FaultDefinition
from services.fault_proxy.app import FAULT_HEADER, create_app


def fault_definition() -> FaultDefinition:
    return FaultDefinition.model_validate(
        {
            "schema_version": "1.0",
            "fault_id": "wrong-item-id",
            "description": "Return a string item ID.",
            "category": "response_body",
            "matcher": {
                "method": "GET",
                "path_regex": r"^/items/[0-9]+$",
                "response_statuses": [200],
                "response_media_type": "application/json",
            },
            "mutation": {
                "type": "replace_json_value",
                "pointer": "/id",
                "value": "wrong",
            },
        }
    )


def upstream_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/items/1":
        return httpx.Response(
            200,
            headers=[
                ("content-type", "application/json"),
                ("set-cookie", "a=1"),
                ("set-cookie", "b=2"),
            ],
            json={"id": 1, "name": "pencil"},
        )
    return httpx.Response(404, json={"detail": "not found"})


def proxy_client(
    handler: httpx.MockTransport | None = None,
    *,
    max_response_bytes: int = 1_048_576,
) -> TestClient:
    application = create_app(
        "http://upstream.test/api",
        faults=[fault_definition()],
        transport=handler or httpx.MockTransport(upstream_handler),
        max_response_bytes=max_response_bytes,
    )
    return TestClient(application)


def test_clean_mode_forwards_response_without_mutation() -> None:
    with proxy_client() as client:
        response = client.get("/items/1")
        state = client.get("/__oate__/state")

    assert response.status_code == 200
    assert response.json() == {"id": 1, "name": "pencil"}
    assert FAULT_HEADER not in response.headers
    assert response.headers.get_list("set-cookie") == ["a=1", "b=2"]
    assert state.json() == {
        "mode": "pass_through",
        "configured_fault_id": None,
        "trigger_count": 0,
    }


def test_active_fault_mutates_matching_response_and_counts_trigger() -> None:
    with proxy_client() as client:
        activated = client.put("/__oate__/faults/wrong-item-id")
        first = client.get("/items/1")
        second = client.get("/items/1")
        state = client.get("/__oate__/state")

    assert activated.json()["trigger_count"] == 0
    assert first.json() == {"id": "wrong", "name": "pencil"}
    assert first.headers[FAULT_HEADER] == "wrong-item-id"
    assert second.headers[FAULT_HEADER] == "wrong-item-id"
    assert state.json() == {
        "mode": "active",
        "configured_fault_id": "wrong-item-id",
        "trigger_count": 2,
    }


def test_active_fault_does_not_count_nonmatching_response() -> None:
    with proxy_client() as client:
        client.put("/__oate__/faults/wrong-item-id")
        response = client.get("/missing")
        state = client.get("/__oate__/state")

    assert response.status_code == 404
    assert FAULT_HEADER not in response.headers
    assert state.json()["trigger_count"] == 0


def test_reset_returns_proxy_to_clean_mode() -> None:
    with proxy_client() as client:
        client.put("/__oate__/faults/wrong-item-id")
        client.get("/items/1")
        reset = client.delete("/__oate__/fault")
        clean = client.get("/items/1")

    assert reset.json() == {
        "mode": "pass_through",
        "configured_fault_id": None,
        "trigger_count": 0,
    }
    assert clean.json()["id"] == 1
    assert FAULT_HEADER not in clean.headers


def test_rejects_unknown_fault_id() -> None:
    with proxy_client() as client:
        response = client.put("/__oate__/faults/not-in-catalog")

    assert response.status_code == 404
    assert response.json() == {"detail": "unknown fault ID"}


def test_forwards_request_path_query_headers_and_body() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"ok": True})

    with proxy_client(httpx.MockTransport(handler)) as client:
        response = client.post(
            "/items?source=test",
            headers={"x-trace": "trace-1"},
            json={"name": "pencil"},
        )

    assert response.status_code == 201
    assert len(captured) == 1
    forwarded = captured[0]
    assert str(forwarded.url) == "http://upstream.test/api/items?source=test"
    assert forwarded.headers["x-trace"] == "trace-1"
    assert json.loads(forwarded.content) == {"name": "pencil"}


def test_returns_502_when_upstream_request_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with proxy_client(httpx.MockTransport(handler)) as client:
        response = client.get("/items/1")

    assert response.status_code == 502
    assert response.json() == {"detail": "upstream request failed"}


def test_returns_502_when_upstream_response_is_too_large() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"12345")

    with proxy_client(httpx.MockTransport(handler), max_response_bytes=4) as client:
        response = client.get("/items/1")

    assert response.status_code == 502
    assert response.json() == {"detail": "upstream response too large"}
