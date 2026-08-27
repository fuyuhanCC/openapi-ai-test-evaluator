from openapi_ai_test_evaluator.domain.execution import ExecutionOutcome
from openapi_ai_test_evaluator.domain.test_plan import Assertion
from openapi_ai_test_evaluator.execution import (
    REDACTED_VALUE,
    OpenAPIValidationIssue,
    OpenAPIValidationSubject,
    ProcessedResponse,
    ResponseBodyKind,
    ResponseData,
    ResponseParseIssue,
    TransportResponse,
    execute_assertions,
)


def assertion(**values: object) -> Assertion:
    return Assertion.model_validate(values)


def processed_response(
    body: object,
    *,
    status_code: int = 200,
    headers: tuple[tuple[str, str], ...] = (("Content-Type", "application/json"),),
    contract_issues: tuple[OpenAPIValidationIssue, ...] = (),
) -> ProcessedResponse:
    raw = TransportResponse(
        status_code=status_code,
        headers=headers,
        body=b"response bytes are not used by assertion tests",
        duration_ms=3,
    )
    data = ResponseData(
        status_code=status_code,
        headers=headers,
        media_type="application/json",
        body_kind=ResponseBodyKind.JSON,
        body=body,  # type: ignore[arg-type]
        duration_ms=3,
    )
    return ProcessedResponse(raw=raw, data=data, contract_issues=contract_issues, parse_issue=None)


def test_executes_status_and_json_equality_with_generated_ids() -> None:
    assertions = [
        assertion(operator="status_is", expected=200),
        assertion(
            operator="equals",
            actual={"source": "response.body", "pointer": "/id"},
            expected=1,
        ),
    ]

    results = execute_assertions(assertions, processed_response({"id": 1}), {})

    assert [result.assertion_id for result in results] == ["assertion-1", "assertion-2"]
    assert [result.outcome for result in results] == [
        ExecutionOutcome.PASSED,
        ExecutionOutcome.PASSED,
    ]


def test_executes_status_membership() -> None:
    plan_assertion = assertion(operator="status_in", expected=[400, 422])

    passed = execute_assertions(
        [plan_assertion], processed_response({}, status_code=422), {}
    )[0]
    failed = execute_assertions(
        [plan_assertion], processed_response({}, status_code=200), {}
    )[0]

    assert passed.outcome is ExecutionOutcome.PASSED
    assert passed.actual == 422
    assert passed.expected == [400, 422]
    assert failed.outcome is ExecutionOutcome.FAILED


def test_distinguishes_existing_json_null_from_missing_pointer() -> None:
    assertions = [
        assertion(
            operator="equals",
            actual={"source": "response.body", "pointer": "/optional"},
            expected=None,
        ),
        assertion(
            operator="exists",
            actual={"source": "response.body", "pointer": "/optional"},
        ),
        assertion(
            operator="exists",
            actual={"source": "response.body", "pointer": "/missing"},
        ),
    ]

    results = execute_assertions(assertions, processed_response({"optional": None}), {})

    assert [result.outcome for result in results] == [
        ExecutionOutcome.PASSED,
        ExecutionOutcome.PASSED,
        ExecutionOutcome.FAILED,
    ]


def test_resolves_runtime_value_and_finds_partial_object_in_array() -> None:
    plan_assertion = assertion(
        operator="contains",
        actual={"source": "response.body", "pointer": "/items"},
        expected={"id": {"$var": "item_id"}},
    )

    result = execute_assertions(
        [plan_assertion],
        processed_response({"items": [{"id": 7, "name": "book"}]}),
        {"item_id": 7},
    )[0]

    assert result.outcome is ExecutionOutcome.PASSED
    assert result.expected == {"id": 7}


def test_executes_remaining_value_operators() -> None:
    assertions = [
        assertion(
            operator="not_equals",
            actual={"source": "response.body", "pointer": "/name"},
            expected="pen",
        ),
        assertion(
            operator="contains",
            actual={"source": "response.body", "pointer": "/name"},
            expected="ook",
        ),
        assertion(
            operator="length_is",
            actual={"source": "response.body", "pointer": "/items"},
            expected=2,
        ),
        assertion(
            operator="greater_than",
            actual={"source": "response.body", "pointer": "/price"},
            expected=9,
        ),
        assertion(
            operator="matches_pattern",
            actual={"source": "response.body", "pointer": "/name"},
            expected="^bo+k$",
        ),
    ]

    results = execute_assertions(
        assertions,
        processed_response({"name": "book", "items": [1, 2], "price": 10.0}),
        {},
    )

    assert all(result.outcome is ExecutionOutcome.PASSED for result in results)


def test_checks_that_collection_item_keys_are_unique() -> None:
    plan_assertion = assertion(
        operator="items_unique_by",
        actual={"source": "response.body", "pointer": "/items"},
        expected="/id",
    )

    passed = execute_assertions(
        [plan_assertion],
        processed_response({"items": [{"id": 1}, {"id": 2}]}),
        {},
    )[0]
    failed = execute_assertions(
        [plan_assertion],
        processed_response({"items": [{"id": 1}, {"id": 1}]}),
        {},
    )[0]

    assert passed.outcome is ExecutionOutcome.PASSED
    assert failed.outcome is ExecutionOutcome.FAILED
    assert failed.message == "items_unique_by assertion failed"


def test_collection_uniqueness_errors_for_missing_item_key() -> None:
    plan_assertion = assertion(
        operator="items_unique_by",
        actual={"source": "response.body", "pointer": "/items"},
        expected="/id",
    )

    result = execute_assertions(
        [plan_assertion],
        processed_response({"items": [{"name": "missing id"}]}),
        {},
    )[0]

    assert result.outcome is ExecutionOutcome.ERROR
    assert result.message == "items_unique_by key is missing from item at index 0"


def test_collection_uniqueness_errors_when_selected_value_is_not_an_array() -> None:
    plan_assertion = assertion(
        operator="items_unique_by",
        actual={"source": "response.body", "pointer": "/items"},
        expected="/id",
    )

    result = execute_assertions(
        [plan_assertion], processed_response({"items": {"id": 1}}), {}
    )[0]

    assert result.outcome is ExecutionOutcome.ERROR
    assert result.message == "items_unique_by actual value is not an array"


def test_selects_headers_case_insensitively_and_preserves_repeated_values() -> None:
    assertions = [
        assertion(
            operator="equals",
            actual={"source": "response.headers", "pointer": "/X-Trace"},
            expected="trace-1",
        ),
        assertion(
            operator="contains",
            actual={"source": "response.headers", "pointer": "/Set-Cookie"},
            expected="second=2",
        ),
    ]
    response = processed_response(
        {},
        headers=(("X-Trace", "trace-1"), ("Set-Cookie", "first=1"), ("Set-Cookie", "second=2")),
    )

    results = execute_assertions(assertions, response, {})

    assert all(result.outcome is ExecutionOutcome.PASSED for result in results)


def test_schema_matches_converts_openapi_details_to_assertion_issues() -> None:
    contract_issue = OpenAPIValidationIssue(
        subject=OpenAPIValidationSubject.RESPONSE,
        error_type="InvalidData",
        message="raw third-party message",
        details={
            "cause_type": "InvalidSchemaValue",
            "schema_errors": [{"message": "wrong type", "path": ["id"]}],
        },
    )

    result = execute_assertions(
        [assertion(operator="schema_matches")],
        processed_response({"id": "wrong"}, contract_issues=(contract_issue,)),
        {},
    )[0]

    assert result.outcome is ExecutionOutcome.FAILED
    assert result.actual is None
    assert result.issues[0].location == "/id"
    assert result.issues[0].keyword == "InvalidSchemaValue"


def test_body_parse_failure_errors_body_assertion_but_not_status_assertion() -> None:
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
    assertions = [
        assertion(operator="status_is", expected=200),
        assertion(
            operator="exists",
            actual={"source": "response.body", "pointer": "/id"},
        ),
    ]

    results = execute_assertions(assertions, response, {})

    assert results[0].outcome is ExecutionOutcome.PASSED
    assert results[1].outcome is ExecutionOutcome.ERROR


def test_unknown_runtime_variable_is_an_assertion_error() -> None:
    plan_assertion = assertion(
        operator="equals",
        actual={"source": "response.body", "pointer": "/id"},
        expected={"$var": "missing"},
    )

    result = execute_assertions([plan_assertion], processed_response({"id": 1}), {})[0]

    assert result.outcome is ExecutionOutcome.ERROR
    assert result.message == "unknown runtime variable 'missing'"


def test_redacts_sensitive_selected_actual_and_expected_values() -> None:
    plan_assertion = assertion(
        operator="equals",
        actual={"source": "response.body", "pointer": "/access_token"},
        expected={"$var": "expected_token"},
    )

    result = execute_assertions(
        [plan_assertion],
        processed_response({"access_token": "unsafe-secret"}),
        {"expected_token": "unsafe-secret"},
    )[0]

    assert result.outcome is ExecutionOutcome.PASSED
    assert result.actual == REDACTED_VALUE
    assert result.expected == REDACTED_VALUE


def test_generated_assertion_id_does_not_collide_with_explicit_id() -> None:
    assertions = [
        assertion(operator="status_is", expected=200),
        assertion(id="assertion-1", operator="status_is", expected=200),
    ]

    results = execute_assertions(assertions, processed_response({}), {})

    assert [result.assertion_id for result in results] == ["assertion-1-2", "assertion-1"]
