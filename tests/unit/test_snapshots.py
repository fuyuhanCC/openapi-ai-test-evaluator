from copy import deepcopy

from openapi_ai_test_evaluator.execution import (
    REDACTED_VALUE,
    PreparedRequest,
    TransportResponse,
    build_request_snapshot,
    build_response_snapshot,
)


def test_builds_and_sanitizes_request_snapshot() -> None:
    body = {
        "name": "Created Item",
        "password": "unsafe-password",
        "nested": {"access_token": "unsafe-token"},
    }
    request = PreparedRequest(
        operation_id="createItem",
        method="POST",
        path="/items",
        query=(("access_token", "unsafe-query-token"), ("tag", "visible")),
        headers={"Authorization": "Bearer unsafe", "X-Trace": "visible"},
        json_body=body,
        timeout_ms=5000,
    )

    snapshot = build_request_snapshot(request)

    assert snapshot.method == "POST"
    assert snapshot.path == "/items"
    assert [(item.name, item.value) for item in snapshot.query] == [
        ("access_token", REDACTED_VALUE),
        ("tag", "visible"),
    ]
    assert snapshot.headers == {
        "authorization": REDACTED_VALUE,
        "x-trace": "visible",
        "content-type": "application/json",
    }
    assert snapshot.body.value == {
        "name": "Created Item",
        "password": REDACTED_VALUE,
        "nested": {"access_token": REDACTED_VALUE},
    }
    assert snapshot.body.size_bytes > 0
    assert snapshot.body.truncated is False


def test_request_snapshot_does_not_mutate_original_body() -> None:
    body = {"password": "unsafe-password"}
    original = deepcopy(body)
    request = PreparedRequest(
        operation_id="createItem",
        method="POST",
        path="/items",
        query=(),
        headers={},
        json_body=body,
        timeout_ms=5000,
    )

    build_request_snapshot(request)

    assert body == original


def test_builds_empty_request_body_snapshot() -> None:
    request = PreparedRequest(
        operation_id="getItem",
        method="GET",
        path="/items/1",
        query=(),
        headers={},
        json_body=None,
        timeout_ms=5000,
    )

    snapshot = build_request_snapshot(request)

    assert snapshot.body.media_type is None
    assert snapshot.body.value is None
    assert snapshot.body.size_bytes == 0
    assert "content-type" not in snapshot.headers


def test_parses_and_sanitizes_json_response_snapshot() -> None:
    response = TransportResponse(
        status_code=200,
        headers=(
            ("Content-Type", "application/json; charset=utf-8"),
            ("Set-Cookie", "session=unsafe"),
            ("X-Trace", "visible"),
        ),
        body=b'{"id":1,"token":"unsafe","nested":{"client_secret":"unsafe"}}',
        duration_ms=10,
    )

    snapshot = build_response_snapshot(response)

    assert snapshot.status_code == 200
    assert snapshot.headers == {
        "content-type": "application/json; charset=utf-8",
        "set-cookie": REDACTED_VALUE,
        "x-trace": "visible",
    }
    assert snapshot.body.media_type == "application/json"
    assert snapshot.body.value == {
        "id": 1,
        "token": REDACTED_VALUE,
        "nested": {"client_secret": REDACTED_VALUE},
    }
    assert snapshot.body.size_bytes == len(response.body)


def test_parses_structured_json_media_type() -> None:
    response = TransportResponse(
        status_code=400,
        headers=(("Content-Type", "application/problem+json"),),
        body=b'{"title":"Invalid request"}',
        duration_ms=1,
    )

    snapshot = build_response_snapshot(response)

    assert snapshot.body.media_type == "application/problem+json"
    assert snapshot.body.value == {"title": "Invalid request"}


def test_omits_invalid_json_text_that_cannot_be_safely_redacted() -> None:
    response = TransportResponse(
        status_code=200,
        headers=(("Content-Type", "application/json"),),
        body=b'{"broken":',
        duration_ms=1,
    )

    snapshot = build_response_snapshot(response)

    assert snapshot.body.media_type == "application/json"
    assert snapshot.body.value == "[UNPARSEABLE JSON BODY OMITTED]"


def test_omits_non_json_body_that_cannot_be_structurally_redacted() -> None:
    response = TransportResponse(
        status_code=500,
        headers=(("Content-Type", "text/plain"),),
        body=b"password=unsafe-password",
        duration_ms=1,
    )

    snapshot = build_response_snapshot(response)

    assert snapshot.body.media_type == "text/plain"
    assert snapshot.body.value == "[NON-JSON BODY OMITTED]"
    assert snapshot.body.size_bytes == len(response.body)


def test_builds_empty_response_body_snapshot() -> None:
    response = TransportResponse(
        status_code=204,
        headers=(),
        body=b"",
        duration_ms=1,
    )

    snapshot = build_response_snapshot(response)

    assert snapshot.body.media_type is None
    assert snapshot.body.value is None
    assert snapshot.body.size_bytes == 0
