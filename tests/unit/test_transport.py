import json

import httpx
import pytest

from openapi_ai_test_evaluator.domain.execution import ErrorCategory
from openapi_ai_test_evaluator.execution import (
    HttpTransport,
    PreparedRequest,
    TransportFailure,
)


def prepared_request(**changes: object) -> PreparedRequest:
    values: dict[str, object] = {
        "operation_id": "createItem",
        "method": "POST",
        "path": "/items",
        "path_parameters": (),
        "query": (("tag", "first"), ("tag", "second")),
        "headers": {"X-Test": "transport"},
        "json_body": {"name": "Created Item"},
        "timeout_ms": 5000,
    }
    values.update(changes)
    return PreparedRequest(**values)  # type: ignore[arg-type]


def test_sends_prepared_request_and_returns_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/items"
        assert request.url.params.multi_items() == [
            ("tag", "first"),
            ("tag", "second"),
        ]
        assert request.headers["x-test"] == "transport"
        assert json.loads(request.content) == {"name": "Created Item"}
        return httpx.Response(
            201,
            headers=[("Content-Type", "application/json"), ("X-Trace", "one")],
            json={"id": 1},
        )

    with HttpTransport(
        "https://example.test/api",
        transport=httpx.MockTransport(handler),
    ) as transport:
        response = transport.send(prepared_request())

    assert response.status_code == 201
    assert ("content-type", "application/json") in response.headers
    assert response.body == b'{"id":1}'
    assert response.duration_ms >= 0


def test_does_not_send_json_null_for_absent_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.content == b""
        assert "content-type" not in request.headers
        return httpx.Response(204)

    with HttpTransport(
        "https://example.test",
        transport=httpx.MockTransport(handler),
    ) as transport:
        response = transport.send(
            prepared_request(method="GET", path="/items/1", query=(), json_body=None)
        )

    assert response.status_code == 204
    assert response.body == b""


def test_does_not_follow_redirects() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": "/elsewhere"})

    with HttpTransport(
        "https://example.test",
        transport=httpx.MockTransport(handler),
    ) as transport:
        response = transport.send(prepared_request())

    assert response.status_code == 302
    assert calls == 1


def test_classifies_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    with HttpTransport(
        "https://example.test",
        transport=httpx.MockTransport(handler),
    ) as transport:
        with pytest.raises(TransportFailure) as caught:
            transport.send(prepared_request())

    assert caught.value.category is ErrorCategory.TIMEOUT
    assert caught.value.location == "transport"


def test_classifies_connection_failure_as_unavailable_sut() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated connection failure", request=request)

    with HttpTransport(
        "https://example.test",
        transport=httpx.MockTransport(handler),
    ) as transport:
        with pytest.raises(TransportFailure) as caught:
            transport.send(prepared_request())

    assert caught.value.category is ErrorCategory.SUT_UNAVAILABLE


def test_rejects_declared_oversized_response_before_reading() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Length": "100"}, content=b"small")

    with HttpTransport(
        "https://example.test",
        max_response_bytes=10,
        transport=httpx.MockTransport(handler),
    ) as transport:
        with pytest.raises(TransportFailure) as caught:
            transport.send(prepared_request())

    assert caught.value.category is ErrorCategory.RESPONSE_TOO_LARGE
    assert caught.value.location == "response.body"


def test_rejects_streamed_response_that_exceeds_limit() -> None:
    class OversizedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"1234"
            yield b"5678"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=OversizedStream())

    with HttpTransport(
        "https://example.test",
        max_response_bytes=6,
        transport=httpx.MockTransport(handler),
    ) as transport:
        with pytest.raises(TransportFailure) as caught:
            transport.send(prepared_request())

    assert caught.value.category is ErrorCategory.RESPONSE_TOO_LARGE


def test_rejects_nonpositive_response_limit() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        HttpTransport("https://example.test", max_response_bytes=0)
