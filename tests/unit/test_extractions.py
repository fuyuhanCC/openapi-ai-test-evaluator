from openapi_ai_test_evaluator.domain.execution import ExtractionStatus
from openapi_ai_test_evaluator.domain.test_plan import Extraction
from openapi_ai_test_evaluator.execution import (
    REDACTED_VALUE,
    ProcessedResponse,
    ResponseBodyKind,
    ResponseData,
    ResponseParseIssue,
    TransportResponse,
    execute_extractions,
)


def extraction(**values: object) -> Extraction:
    return Extraction.model_validate(values)


def processed_response(
    body: object,
    *,
    headers: tuple[tuple[str, str], ...] = (("Content-Type", "application/json"),),
) -> ProcessedResponse:
    raw = TransportResponse(
        status_code=200,
        headers=headers,
        body=b"response bytes are not used by extraction tests",
        duration_ms=3,
    )
    data = ResponseData(
        status_code=200,
        headers=headers,
        media_type="application/json",
        body_kind=ResponseBodyKind.JSON,
        body=body,  # type: ignore[arg-type]
        duration_ms=3,
    )
    return ProcessedResponse(raw=raw, data=data, contract_issues=(), parse_issue=None)


def test_extracts_body_value_into_result_and_runtime_values() -> None:
    batch = execute_extractions(
        [extraction(variable="item_id", source="response.body", pointer="/id")],
        processed_response({"id": 7}),
    )

    assert batch.results[0].status is ExtractionStatus.EXTRACTED
    assert batch.results[0].value == 7
    assert batch.values == (("item_id", 7),)
    assert batch.issues == ()


def test_distinguishes_json_null_from_a_missing_value() -> None:
    batch = execute_extractions(
        [
            extraction(variable="optional", source="response.body", pointer="/optional"),
            extraction(
                variable="absent",
                source="response.body",
                pointer="/absent",
                required=False,
            ),
        ],
        processed_response({"optional": None}),
    )

    assert [result.status for result in batch.results] == [
        ExtractionStatus.EXTRACTED,
        ExtractionStatus.MISSING,
    ]
    assert batch.values == (("optional", None),)
    assert batch.issues == ()


def test_only_required_missing_extraction_produces_an_issue() -> None:
    batch = execute_extractions(
        [
            extraction(variable="required_id", source="response.body", pointer="/id"),
            extraction(
                variable="optional_name",
                source="response.body",
                pointer="/name",
                required=False,
            ),
        ],
        processed_response({}),
    )

    assert all(result.status is ExtractionStatus.MISSING for result in batch.results)
    assert batch.values == ()
    assert len(batch.issues) == 1
    assert batch.issues[0].variable == "required_id"
    assert batch.issues[0].message == "required response value is missing"


def test_selects_headers_case_insensitively_and_preserves_repeated_values() -> None:
    batch = execute_extractions(
        [extraction(variable="traces", source="response.headers", pointer="/X-Trace")],
        processed_response(
            {},
            headers=(("X-Trace", "trace-1"), ("X-Trace", "trace-2")),
        ),
    )

    assert batch.results[0].value == ["trace-1", "trace-2"]
    assert batch.values == (("traces", ["trace-1", "trace-2"]),)


def test_redacts_stored_nested_secret_but_preserves_raw_runtime_value() -> None:
    raw_value = {"id": 7, "credentials": {"access_token": "unsafe-secret"}}

    batch = execute_extractions(
        [extraction(variable="item", source="response.body", pointer="")],
        processed_response(raw_value),
    )

    assert batch.results[0].value == {
        "id": 7,
        "credentials": {"access_token": REDACTED_VALUE},
    }
    assert batch.results[0].redacted is True
    assert batch.values == (("item", raw_value),)


def test_redacts_a_value_selected_by_a_sensitive_header_name() -> None:
    batch = execute_extractions(
        [
            extraction(
                variable="auth",
                source="response.headers",
                pointer="/Authorization",
            )
        ],
        processed_response({}, headers=(("Authorization", "Bearer unsafe-secret"),)),
    )

    assert batch.results[0].value == REDACTED_VALUE
    assert batch.results[0].redacted is True
    assert batch.values == (("auth", "Bearer unsafe-secret"),)


def test_body_parse_failure_is_an_extraction_error() -> None:
    raw = TransportResponse(status_code=200, headers=(), body=b'{"broken":', duration_ms=1)
    response = ProcessedResponse(
        raw=raw,
        data=None,
        contract_issues=(),
        parse_issue=ResponseParseIssue(
            location="response.body",
            message="response declares JSON but contains invalid JSON",
        ),
    )

    batch = execute_extractions(
        [extraction(variable="item_id", source="response.body", pointer="/id")],
        response,
    )

    assert batch.results[0].status is ExtractionStatus.ERROR
    assert batch.results[0].value is None
    assert batch.values == ()
    assert len(batch.issues) == 1
    assert "response body is unavailable" in batch.issues[0].message


def test_invalid_json_pointer_escape_is_an_extraction_error() -> None:
    batch = execute_extractions(
        [extraction(variable="item_id", source="response.body", pointer="/bad~2pointer")],
        processed_response({}),
    )

    assert batch.results[0].status is ExtractionStatus.ERROR
    assert batch.issues[0].message == "invalid JSON Pointer escape"
