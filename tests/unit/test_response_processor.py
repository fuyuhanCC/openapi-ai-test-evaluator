import json
from pathlib import Path

import pytest

from openapi_ai_test_evaluator.execution import (
    OpenAPIContractValidator,
    PreparedRequest,
    ProcessedResponse,
    ResponseBodyKind,
    ResponseParseIssue,
    TransportResponse,
    process_response,
)
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")


def prepared_request() -> PreparedRequest:
    return PreparedRequest(
        operation_id="getItem",
        method="GET",
        path="/items/1",
        path_parameters=(("itemId", "1"),),
        query=(),
        headers={},
        has_json_body=False,
        json_body=None,
        timeout_ms=5000,
    )


def response(body: bytes, *, content_type: str = "application/json") -> TransportResponse:
    return TransportResponse(
        status_code=200,
        headers=(("Content-Type", content_type), ("X-Trace", "trace-1")),
        body=body,
        duration_ms=3,
    )


def validator() -> OpenAPIContractValidator:
    return OpenAPIContractValidator(SPEC, "http://fault-proxy.test:9000/api")


def valid_item_body() -> bytes:
    return json.dumps(
        {
            "id": 1,
            "name": "book",
            "price": 10.0,
            "status": "active",
            "createdAt": "2026-08-21T10:00:00Z",
            "updatedAt": "2026-08-21T10:00:00Z",
        },
        separators=(",", ":"),
    ).encode()


def test_combines_successful_contract_validation_and_parsing() -> None:
    raw = response(valid_item_body())

    processed = process_response(prepared_request(), raw, validator())

    assert processed.raw is raw
    assert processed.contract_issues == ()
    assert processed.parse_issue is None
    assert processed.data is not None
    assert processed.data.body_kind is ResponseBodyKind.JSON
    assert isinstance(processed.data.body, dict)
    assert processed.data.body["id"] == 1


def test_keeps_parsed_data_when_openapi_schema_validation_fails() -> None:
    raw = response(b'{"id":"wrong"}')

    processed = process_response(prepared_request(), raw, validator())

    assert processed.data is not None
    assert processed.data.body == {"id": "wrong"}
    assert processed.parse_issue is None
    assert [issue.error_type for issue in processed.contract_issues] == ["InvalidData"]


def test_keeps_raw_response_and_both_issues_when_json_is_invalid() -> None:
    raw = response(b'{"broken":')

    processed = process_response(prepared_request(), raw, validator())

    assert processed.raw is raw
    assert processed.raw.status_code == 200
    assert ("X-Trace", "trace-1") in processed.raw.headers
    assert processed.data is None
    assert processed.parse_issue == ResponseParseIssue(
        location="response.body",
        message="response declares JSON but contains invalid JSON",
    )
    assert [issue.error_type for issue in processed.contract_issues] == ["DataValidationError"]


def test_keeps_text_data_when_media_type_violates_openapi_contract() -> None:
    processed = process_response(
        prepared_request(),
        response(b"not json", content_type="text/plain"),
        validator(),
    )

    assert processed.data is not None
    assert processed.data.body_kind is ResponseBodyKind.TEXT
    assert processed.data.body == "not json"
    assert processed.parse_issue is None
    assert processed.contract_issues


def test_processed_response_requires_data_or_parse_issue_but_not_both() -> None:
    raw = response(valid_item_body())
    parse_issue = ResponseParseIssue(location="response.body", message="invalid")

    with pytest.raises(ValueError, match="exactly one"):
        ProcessedResponse(raw=raw, data=None, contract_issues=(), parse_issue=None)

    with pytest.raises(ValueError, match="exactly one"):
        ProcessedResponse(
            raw=raw,
            data=process_response(prepared_request(), raw, validator()).data,
            contract_issues=(),
            parse_issue=parse_issue,
        )
