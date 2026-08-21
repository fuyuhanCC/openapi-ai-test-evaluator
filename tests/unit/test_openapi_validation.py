import json
from pathlib import Path

import pytest

from openapi_ai_test_evaluator.execution import (
    OpenAPIContractValidator,
    OpenAPIValidationSubject,
    PreparedRequest,
    TransportResponse,
)
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]


def prepared_request(**changes: object) -> PreparedRequest:
    values: dict[str, object] = {
        "operation_id": "getItem",
        "method": "GET",
        "path": "/items/1",
        "path_parameters": (("itemId", "1"),),
        "query": (),
        "headers": {},
        "json_body": None,
        "timeout_ms": 5000,
    }
    values.update(changes)
    return PreparedRequest(**values)  # type: ignore[arg-type]


def json_response(body: object, status_code: int = 200) -> TransportResponse:
    return TransportResponse(
        status_code=status_code,
        headers=(("Content-Type", "application/json"),),
        body=json.dumps(body, separators=(",", ":")).encode(),
        duration_ms=2,
    )


@pytest.mark.parametrize("spec_name", ["openapi.yaml", "openapi-3.1.yaml"])
def test_accepts_conformant_request_for_openapi_30_and_31(spec_name: str) -> None:
    spec = load_openapi(ROOT / "examples" / "demo-items" / spec_name)
    validator = OpenAPIContractValidator(spec, "http://fault-proxy.test:9000/api")

    issues = validator.validate_request(prepared_request())

    assert issues == ()


def test_reports_invalid_request_parameter() -> None:
    spec = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")
    validator = OpenAPIContractValidator(spec, "http://127.0.0.1:8000")
    request = prepared_request(
        path="/items/not-an-integer",
        path_parameters=(("itemId", "not-an-integer"),),
    )

    issues = validator.validate_request(request)

    assert len(issues) == 1
    assert issues[0].subject is OpenAPIValidationSubject.REQUEST
    assert issues[0].error_type == "ParameterValidationError"
    assert "itemId" in issues[0].message
    assert issues[0].details is not None
    assert issues[0].details["cause_type"] == "CastError"


@pytest.mark.parametrize("spec_name", ["openapi.yaml", "openapi-3.1.yaml"])
def test_accepts_conformant_response_for_openapi_30_and_31(spec_name: str) -> None:
    spec = load_openapi(ROOT / "examples" / "demo-items" / spec_name)
    validator = OpenAPIContractValidator(spec, "http://fault-proxy.test:9000/api")
    response = json_response(
        {
            "id": 1,
            "name": "book",
            "price": 10.0,
            "status": "active",
            "createdAt": "2026-08-21T10:00:00Z",
            "updatedAt": "2026-08-21T10:00:00Z",
        }
    )

    issues = validator.validate_response(prepared_request(), response)

    assert issues == ()


def test_reports_structured_response_schema_issue() -> None:
    spec = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")
    validator = OpenAPIContractValidator(spec, "http://127.0.0.1:8000")

    issues = validator.validate_response(
        prepared_request(),
        json_response({"id": "wrong"}),
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue.subject is OpenAPIValidationSubject.RESPONSE
    assert issue.error_type == "InvalidData"
    assert issue.details is not None
    assert issue.details["error_type"] == "InvalidData"
    schema_errors = issue.details["schema_errors"]
    assert isinstance(schema_errors, list)
    assert {tuple(error["path"]) for error in schema_errors} >= {(), ("id",)}


def test_reports_undeclared_response_status() -> None:
    spec = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")
    validator = OpenAPIContractValidator(spec, "http://127.0.0.1:8000")

    issues = validator.validate_response(
        prepared_request(),
        json_response({"code": "teapot", "message": "unexpected"}, status_code=418),
    )

    assert len(issues) == 1
    assert issues[0].error_type == "ResponseNotFound"


def test_rejects_unknown_prepared_operation() -> None:
    spec = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")
    validator = OpenAPIContractValidator(spec, "http://127.0.0.1:8000")

    with pytest.raises(ValueError, match="unknown operation"):
        validator.validate_request(prepared_request(operation_id="unknown"))
