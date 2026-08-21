from pathlib import Path

import pytest
from openapi_core.protocols import Request, Response

from openapi_ai_test_evaluator.execution import (
    PreparedRequest,
    TransportResponse,
    adapt_openapi_request,
    adapt_openapi_response,
)
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")


def prepared_request(**changes: object) -> PreparedRequest:
    values: dict[str, object] = {
        "operation_id": "getItem",
        "method": "GET",
        "path": "/items/folder%2Fitem%201",
        "path_parameters": (("itemId", "folder/item 1"),),
        "query": (("tag", "first"), ("tag", "second")),
        "headers": {"X-Test": "adapter"},
        "json_body": None,
        "timeout_ms": 5000,
    }
    values.update(changes)
    return PreparedRequest(**values)  # type: ignore[arg-type]


def test_adapts_prepared_request_to_openapi_core_protocol() -> None:
    adapter = adapt_openapi_request(
        prepared_request(
            operation_id="updateItem",
            method="PATCH",
            headers={"X-Test": "adapter"},
            json_body={"name": "café"},
        ),
        SPEC.operations["updateItem"],
        "https://example.test/api/",
    )

    assert isinstance(adapter, Request)
    assert adapter.host_url == "https://example.test"
    assert adapter.path == "/api/items/folder%2Fitem%201"
    assert adapter.path_pattern == "/api/items/{itemId}"
    assert adapter.method == "patch"
    assert adapter.parameters.path == {"itemId": "folder/item 1"}
    assert adapter.parameters.query.getlist("tag") == ["first", "second"]
    assert adapter.parameters.header["X-Test"] == "adapter"
    assert adapter.parameters.header["Content-Type"] == "application/json"
    assert adapter.parameters.cookie == {}
    assert adapter.content_type == "application/json"
    assert adapter.body == b'{"name":"caf\xc3\xa9"}'


def test_preserves_explicit_request_content_type() -> None:
    adapter = adapt_openapi_request(
        prepared_request(headers={"Content-Type": "Application/JSON; Charset=UTF-8"}),
        SPEC.operations["getItem"],
        "https://example.test",
    )

    assert adapter.content_type == "application/json; charset=utf-8"
    assert adapter.body is None


def test_rejects_mismatched_operation() -> None:
    with pytest.raises(ValueError, match="operation IDs do not match"):
        adapt_openapi_request(
            prepared_request(),
            SPEC.operations["deleteItem"],
            "https://example.test",
        )


@pytest.mark.parametrize(
    "base_url",
    ["example.test/api", "ftp://example.test/api", "https://example.test/api?debug=1"],
)
def test_rejects_invalid_target_base_url(base_url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        adapt_openapi_request(
            prepared_request(),
            SPEC.operations["getItem"],
            base_url,
        )


def test_adapts_transport_response_to_openapi_core_protocol() -> None:
    raw = TransportResponse(
        status_code=200,
        headers=(
            ("Content-Type", "Application/Problem+JSON; Charset=UTF-8"),
            ("Set-Cookie", "first=1"),
            ("Set-Cookie", "second=2"),
        ),
        body=b'{"title":"invalid"}',
        duration_ms=9,
    )

    adapter = adapt_openapi_response(raw)

    assert isinstance(adapter, Response)
    assert adapter.status_code == 200
    assert adapter.content_type == "application/problem+json; charset=utf-8"
    assert adapter.headers.getlist("Set-Cookie") == ["first=1", "second=2"]
    assert adapter.data is raw.body


def test_preserves_empty_response_body() -> None:
    adapter = adapt_openapi_response(
        TransportResponse(status_code=204, headers=(), body=b"", duration_ms=1)
    )

    assert adapter.content_type == ""
    assert adapter.data == b""
