"""Deterministic execution of allowlisted TestPlan assertions."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import cast

from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.execution import (
    AssertionIssue,
    AssertionResult,
    ExecutionOutcome,
)
from openapi_ai_test_evaluator.domain.test_plan import (
    Assertion,
    AssertionOperator,
    ResponseSelector,
)
from openapi_ai_test_evaluator.execution.response_parser import ResponseBodyKind
from openapi_ai_test_evaluator.execution.response_processor import ProcessedResponse
from openapi_ai_test_evaluator.execution.snapshots import (
    REDACTED_VALUE,
    is_sensitive_name,
    sanitize_json_value,
)

_MISSING = object()


class _AssertionEvaluationError(ValueError):
    pass


def execute_assertions(
    assertions: Sequence[Assertion],
    response: ProcessedResponse,
    variables: Mapping[str, JsonValue],
) -> tuple[AssertionResult, ...]:
    """Execute assertions in plan order and return one result for each assertion."""
    assertion_ids = _assign_assertion_ids(assertions)
    return tuple(
        _execute_assertion(assertion_id, assertion, response, variables)
        for assertion_id, assertion in zip(assertion_ids, assertions, strict=True)
    )


def _execute_assertion(
    assertion_id: str,
    assertion: Assertion,
    response: ProcessedResponse,
    variables: Mapping[str, JsonValue],
) -> AssertionResult:
    if assertion.operator is AssertionOperator.STATUS_IS:
        actual = response.raw.status_code
        expected = cast(JsonValue, assertion.expected)
        return _predicate_result(
            assertion_id,
            assertion,
            actual,
            expected,
            actual == expected,
        )

    if assertion.operator is AssertionOperator.SCHEMA_MATCHES:
        issues = _contract_assertion_issues(response)
        return AssertionResult(
            assertion_id=assertion_id,
            operator=assertion.operator,
            outcome=ExecutionOutcome.FAILED if issues else ExecutionOutcome.PASSED,
            actual=None,
            expected=None,
            message="response does not match the OpenAPI contract" if issues else None,
            issues=issues,
        )

    assert assertion.actual is not None
    try:
        expected = (
            None
            if assertion.operator is AssertionOperator.EXISTS
            else _resolve_runtime_value(cast(JsonValue, assertion.expected), variables)
        )
        actual = _select_actual(assertion.actual, response)
        if actual is _MISSING:
            return AssertionResult(
                assertion_id=assertion_id,
                operator=assertion.operator,
                outcome=ExecutionOutcome.FAILED,
                actual=None,
                expected=_stored_value(expected, assertion.actual),
                message="selected response value is missing",
                issues=[],
            )
        actual_value = cast(JsonValue, actual)
        passed = _evaluate_predicate(assertion.operator, actual_value, expected)
    except _AssertionEvaluationError as error:
        return AssertionResult(
            assertion_id=assertion_id,
            operator=assertion.operator,
            outcome=ExecutionOutcome.ERROR,
            actual=None,
            expected=_stored_value(assertion.expected, assertion.actual),
            message=str(error),
            issues=[],
        )

    return _predicate_result(
        assertion_id,
        assertion,
        actual_value,
        expected,
        passed,
    )


def _predicate_result(
    assertion_id: str,
    assertion: Assertion,
    actual: JsonValue,
    expected: JsonValue | None,
    passed: bool,
) -> AssertionResult:
    return AssertionResult(
        assertion_id=assertion_id,
        operator=assertion.operator,
        outcome=ExecutionOutcome.PASSED if passed else ExecutionOutcome.FAILED,
        actual=_stored_value(actual, assertion.actual),
        expected=_stored_value(expected, assertion.actual),
        message=None if passed else f"{assertion.operator.value} assertion failed",
        issues=[],
    )


def _select_actual(
    selector: ResponseSelector,
    response: ProcessedResponse,
) -> JsonValue | object:
    if selector.source == "response.status":
        return response.raw.status_code

    if selector.source == "response.headers":
        assert selector.pointer is not None
        header_value = _headers_as_json(response.raw.headers)
        return _value_at_pointer(header_value, selector.pointer, casefold_first_token=True)

    assert selector.source == "response.body"
    assert selector.pointer is not None
    if response.data is None:
        assert response.parse_issue is not None
        raise _AssertionEvaluationError(
            f"response body is unavailable: {response.parse_issue.message}"
        )
    if response.data.body_kind is ResponseBodyKind.EMPTY:
        return _MISSING
    if response.data.body_kind is ResponseBodyKind.BINARY:
        raise _AssertionEvaluationError("binary response bodies cannot be selected")
    return _value_at_pointer(response.data.body, selector.pointer)


def _headers_as_json(headers: tuple[tuple[str, str], ...]) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {}
    for name, value in headers:
        key = name.casefold()
        existing = values.get(key, _MISSING)
        if existing is _MISSING:
            values[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            values[key] = [cast(JsonValue, existing), value]
    return values


def _value_at_pointer(
    value: JsonValue,
    pointer: str,
    *,
    casefold_first_token: bool = False,
) -> JsonValue | object:
    tokens = _pointer_tokens(pointer)
    if casefold_first_token and tokens:
        tokens[0] = tokens[0].casefold()

    current: JsonValue = value
    for token in tokens:
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
            continue
        if isinstance(current, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                return _MISSING
            index = int(token)
            if index >= len(current):
                return _MISSING
            current = current[index]
            continue
        return _MISSING
    return current


def _pointer_tokens(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise _AssertionEvaluationError("invalid JSON Pointer")
    return [_decode_pointer_token(token) for token in pointer[1:].split("/")]


def _decode_pointer_token(token: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        if token[index] != "~":
            decoded.append(token[index])
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise _AssertionEvaluationError("invalid JSON Pointer escape")
        decoded.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(decoded)


def _evaluate_predicate(
    operator: AssertionOperator,
    actual: JsonValue,
    expected: JsonValue | None,
) -> bool:
    if operator is AssertionOperator.EXISTS:
        return True
    if operator is AssertionOperator.EQUALS:
        return _json_equal(actual, expected)
    if operator is AssertionOperator.NOT_EQUALS:
        return not _json_equal(actual, expected)
    if operator is AssertionOperator.CONTAINS:
        return _contains(actual, expected)
    if operator is AssertionOperator.LENGTH_IS:
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            raise _AssertionEvaluationError(
                "length_is expected value is not a non-negative integer"
            )
        if not isinstance(actual, (str, list, dict)):
            raise _AssertionEvaluationError("length_is actual value has no supported length")
        return len(actual) == expected
    if operator is AssertionOperator.GREATER_THAN:
        if not _is_number(expected):
            raise _AssertionEvaluationError("greater_than expected value is not numeric")
        if not _is_number(actual):
            raise _AssertionEvaluationError("greater_than actual value is not numeric")
        return actual > expected
    if operator is AssertionOperator.MATCHES_PATTERN:
        if not isinstance(expected, str):
            raise _AssertionEvaluationError("matches_pattern expected value is not a string")
        if not isinstance(actual, str):
            raise _AssertionEvaluationError("matches_pattern actual value is not a string")
        try:
            return re.search(expected, actual) is not None
        except re.error as error:
            raise _AssertionEvaluationError(
                "matches_pattern expected value is not a valid regular expression"
            ) from error
    raise _AssertionEvaluationError(f"unsupported assertion operator {operator.value!r}")


def _contains(actual: JsonValue, expected: JsonValue | None) -> bool:
    if isinstance(actual, str):
        if not isinstance(expected, str):
            raise _AssertionEvaluationError("string contains requires a string expected value")
        return expected in actual
    if isinstance(actual, list):
        return any(_json_contains(item, expected) for item in actual)
    if isinstance(actual, dict):
        if isinstance(expected, str):
            return expected in actual
        if isinstance(expected, dict):
            return _json_contains(actual, expected)
        raise _AssertionEvaluationError(
            "object contains requires a string key or partial object expected value"
        )
    raise _AssertionEvaluationError("contains actual value must be a string, array, or object")


def _json_contains(actual: JsonValue, expected: JsonValue | None) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _json_contains(actual[key], nested)
            for key, nested in expected.items()
        )
    return _json_equal(actual, expected)


def _json_equal(left: JsonValue, right: JsonValue | None) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if _is_number(left) and _is_number(right):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    return type(left) is type(right) and left == right


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _resolve_runtime_value(
    value: JsonValue,
    variables: Mapping[str, JsonValue],
) -> JsonValue:
    if isinstance(value, dict):
        if set(value) == {"$var"}:
            variable_name = value["$var"]
            if not isinstance(variable_name, str) or variable_name not in variables:
                raise _AssertionEvaluationError(f"unknown runtime variable {variable_name!r}")
            return variables[variable_name]
        return {key: _resolve_runtime_value(nested, variables) for key, nested in value.items()}
    if isinstance(value, list):
        return [_resolve_runtime_value(nested, variables) for nested in value]
    return value


def _contract_assertion_issues(response: ProcessedResponse) -> list[AssertionIssue]:
    issues: list[AssertionIssue] = []
    for contract_issue in response.contract_issues:
        details = contract_issue.details or {}
        schema_errors = details.get("schema_errors")
        if isinstance(schema_errors, list) and schema_errors:
            for schema_error in schema_errors:
                if not isinstance(schema_error, dict):
                    continue
                path = schema_error.get("path")
                location = _path_to_pointer(path) if isinstance(path, list) else ""
                sensitive = any(is_sensitive_name(str(token)) for token in path or [])
                issues.append(
                    AssertionIssue(
                        location=location,
                        keyword=str(details.get("cause_type") or contract_issue.error_type),
                        message=(
                            "schema validation failed at a sensitive field"
                            if sensitive
                            else str(schema_error.get("message") or "schema validation failed")
                        ),
                    )
                )
            continue
        issues.append(
            AssertionIssue(
                location="",
                keyword=contract_issue.error_type,
                message=f"OpenAPI contract validation failed ({contract_issue.error_type})",
            )
        )
    if response.parse_issue is not None and not issues:
        issues.append(
            AssertionIssue(
                location="",
                keyword="response_parse",
                message=response.parse_issue.message,
            )
        )
    return issues


def _path_to_pointer(path: list[object]) -> str:
    return "".join(f"/{str(token).replace('~', '~0').replace('/', '~1')}" for token in path)


def _stored_value(
    value: JsonValue | None,
    selector: ResponseSelector | None,
) -> JsonValue | None:
    if selector is not None and _selector_is_sensitive(selector):
        return REDACTED_VALUE
    return sanitize_json_value(value)


def _selector_is_sensitive(selector: ResponseSelector) -> bool:
    if selector.source == "response.status" or not selector.pointer:
        return False
    try:
        tokens = _pointer_tokens(selector.pointer)
    except _AssertionEvaluationError:
        return False
    return bool(tokens) and is_sensitive_name(tokens[-1])


def _assign_assertion_ids(assertions: Sequence[Assertion]) -> tuple[str, ...]:
    reserved = {assertion.id for assertion in assertions if assertion.id is not None}
    assigned: list[str] = []
    for index, assertion in enumerate(assertions, start=1):
        if assertion.id is not None:
            assigned.append(assertion.id)
            continue
        candidate = f"assertion-{index}"
        suffix = 2
        while candidate in reserved:
            candidate = f"assertion-{index}-{suffix}"
            suffix += 1
        reserved.add(candidate)
        assigned.append(candidate)
    return tuple(assigned)
