from copy import deepcopy
from pathlib import Path

import pytest

from openapi_ai_test_evaluator.domain.test_case import (
    AssertionOperator,
    RequestMode,
)
from openapi_ai_test_evaluator.domain.test_case import (
    TestCaseBatch as CaseBatch,
)
from openapi_ai_test_evaluator.generation import (
    AdaptationRejectionCode,
    CapturedGenerationMode,
    CapturedPhase,
    CapturedSchemathesisCase,
    adapt_schemathesis_case,
)
from openapi_ai_test_evaluator.spec import load_openapi
from openapi_ai_test_evaluator.validation import validate_test_case_batch_semantics

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")


def captured_case(**changes: object) -> CapturedSchemathesisCase:
    values: dict[str, object] = {
        "case_id": "schemathesis-1",
        "operation_id": "createItem",
        "method": "POST",
        "path": "/items",
        "mode": CapturedGenerationMode.POSITIVE,
        "phase": CapturedPhase.COVERAGE,
        "body_present": True,
        "body": {"name": "book", "price": 10.0, "status": "active"},
        "media_type": "application/json",
    }
    values.update(changes)
    return CapturedSchemathesisCase(**values)  # type: ignore[arg-type]


def rejection_code(adaptation) -> AdaptationRejectionCode:
    assert adaptation.case is None
    assert adaptation.rejections
    return adaptation.rejections[0].code


def test_adapts_a_positive_json_request_to_one_runner_step() -> None:
    adaptation = adapt_schemathesis_case(captured_case(), SPEC)

    assert adaptation.succeeded is True
    assert adaptation.rejections == ()
    assert adaptation.case is not None
    assert adaptation.case.tags == [
        "source:schemathesis",
        "phase:coverage",
        "mode:positive",
    ]
    step = adaptation.case.steps[0]
    assert step.operation_id == "createItem"
    assert step.request.mode is RequestMode.CONFORMANT
    assert step.request.body == {"name": "book", "price": 10.0, "status": "active"}
    assert [assertion.operator for assertion in step.assertions] == [
        AssertionOperator.STATUS_IS,
        AssertionOperator.SCHEMA_MATCHES,
    ]
    assert step.assertions[0].expected == 201
    assert validate_test_case_batch_semantics(
        CaseBatch(schema_version="1.0", cases=[adaptation.case]),
        SPEC,
    ) == []


def test_positive_resource_request_accepts_success_or_not_found() -> None:
    adaptation = adapt_schemathesis_case(
        captured_case(
            operation_id="getItem",
            method="GET",
            path="/items/{itemId}",
            body_present=False,
            body=None,
            media_type=None,
            path_parameters=(("itemId", 999),),
        ),
        SPEC,
    )

    assert adaptation.case is not None
    assertion = adaptation.case.steps[0].assertions[0]
    assert assertion.operator is AssertionOperator.STATUS_IN
    assert assertion.expected == [200, 404]


def test_adapts_a_negative_request_with_inferred_violations_and_status_set() -> None:
    adaptation = adapt_schemathesis_case(
        captured_case(
            operation_id="replaceItem",
            method="PUT",
            path="/items/{itemId}",
            mode=CapturedGenerationMode.NEGATIVE,
            path_parameters=(("itemId", 1),),
            body={"name": "book", "price": 10.0},
        ),
        SPEC,
    )

    assert adaptation.succeeded is True
    assert adaptation.case is not None
    step = adaptation.case.steps[0]
    assert step.request.mode is RequestMode.INTENTIONALLY_INVALID
    serialized_violations = [
        violation.model_dump(mode="json") for violation in step.request.expected_violations
    ]
    assert serialized_violations == [
        {"code": "missing_required", "location": "body", "field": "status"}
    ]
    assert step.assertions[0].operator is AssertionOperator.STATUS_IN
    assert step.assertions[0].expected == [400, 404]
    assert validate_test_case_batch_semantics(
        CaseBatch(schema_version="1.0", cases=[adaptation.case]),
        SPEC,
    ) == []


def test_preserves_explicit_json_null_as_a_negative_type_violation() -> None:
    adaptation = adapt_schemathesis_case(
        captured_case(
            mode=CapturedGenerationMode.NEGATIVE,
            body=None,
        ),
        SPEC,
    )

    assert adaptation.case is not None
    request = adaptation.case.steps[0].request
    assert request.body_present is True
    assert request.body is None
    assert [violation.model_dump(mode="json") for violation in request.expected_violations] == [
        {"code": "type_mismatch", "location": "body", "field": "$body"}
    ]


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"method": "TRACE"}, AdaptationRejectionCode.METHOD_MISMATCH),
        ({"path": "/other"}, AdaptationRejectionCode.PATH_MISMATCH),
        ({"cookies": (("session", "unsafe"),)}, AdaptationRejectionCode.COOKIES_UNSUPPORTED),
        (
            {"media_type": "application/octet-stream"},
            AdaptationRejectionCode.MEDIA_TYPE_UNSUPPORTED,
        ),
        ({"body": b"not-json"}, AdaptationRejectionCode.VALUE_NOT_JSON),
        (
            {"body": {"$var": "not-a-runtime-reference"}},
            AdaptationRejectionCode.RESERVED_RUNTIME_REFERENCE,
        ),
        (
            {
                "mode": CapturedGenerationMode.NEGATIVE,
                "body": {"name": "book", "price": 10.0, "status": "active"},
            },
            AdaptationRejectionCode.NEGATIVE_REQUEST_VALID,
        ),
        (
            {"body": {"name": "book", "price": 10.0}},
            AdaptationRejectionCode.POSITIVE_REQUEST_INVALID,
        ),
        ({"case_id": "INVALID"}, AdaptationRejectionCode.CASE_CONTRACT_INVALID),
    ],
)
def test_rejects_cases_that_cannot_be_faithfully_adapted(
    changes: dict[str, object],
    expected_code: AdaptationRejectionCode,
) -> None:
    adaptation = adapt_schemathesis_case(captured_case(**deepcopy(changes)), SPEC)

    assert rejection_code(adaptation) is expected_code


def test_preserves_semantic_blocker_code_for_adapter_metrics() -> None:
    adaptation = adapt_schemathesis_case(
        captured_case(
            operation_id="listItems",
            method="GET",
            path="/items",
            body_present=False,
            body=None,
            media_type=None,
            query=(("surprise", "yes"),),
        ),
        SPEC,
    )

    assert rejection_code(adaptation) is AdaptationRejectionCode.REQUEST_SEMANTICS_UNSUPPORTED
    assert adaptation.rejections[0].detail_code == "unknown_parameter"


def test_rejects_negative_case_without_a_declared_status_oracle() -> None:
    spec_without_bad_request = SPEC.model_copy(deep=True)
    spec_without_bad_request.operations["listItems"].responses.pop("400")

    adaptation = adapt_schemathesis_case(
        captured_case(
            operation_id="listItems",
            method="GET",
            path="/items",
            mode=CapturedGenerationMode.NEGATIVE,
            body_present=False,
            body=None,
            media_type=None,
            query=(("limit", 0),),
        ),
        spec_without_bad_request,
    )

    assert rejection_code(adaptation) is AdaptationRejectionCode.STATUS_ORACLE_UNAVAILABLE
