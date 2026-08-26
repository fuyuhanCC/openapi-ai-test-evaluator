import json

import pytest

from openapi_ai_test_evaluator.domain.fault import FaultDefinition
from services.fault_proxy.mutations import (
    FaultApplicationReason,
    FaultRequestContext,
    FaultResponse,
    apply_response_fault,
)


def definition(
    mutation: dict[str, object],
    *,
    method: str = "GET",
    path_regex: str = r"^/items/[0-9]+$",
    statuses: list[int] | None = None,
    media_type: str | None = "application/json",
) -> FaultDefinition:
    return FaultDefinition.model_validate(
        {
            "schema_version": "1.0",
            "fault_id": "demo-fault",
            "description": "Deterministic test fault.",
            "category": ("status" if mutation["type"] == "replace_status" else "response_body"),
            "matcher": {
                "method": method,
                "path_regex": path_regex,
                "response_statuses": [200] if statuses is None else statuses,
                "response_media_type": media_type,
            },
            "mutation": mutation,
        }
    )


def request(*, method: str = "GET", path: str = "/items/1") -> FaultRequestContext:
    return FaultRequestContext(method=method, path=path)  # type: ignore[arg-type]


def response(
    body: object = None,
    *,
    status_code: int = 200,
    media_type: str = "application/json; charset=utf-8",
) -> FaultResponse:
    value = {"id": 1, "name": "pencil", "items": [{"id": 1}]} if body is None else body
    encoded = json.dumps(value).encode()
    return FaultResponse(
        status_code=status_code,
        headers=(("content-type", media_type), ("content-length", str(len(encoded)))),
        body=encoded,
    )


def decoded(application_body: bytes) -> object:
    return json.loads(application_body)


def test_replaces_status_without_changing_body() -> None:
    original = response()

    result = apply_response_fault(
        definition({"type": "replace_status", "status_code": 500}),
        request(),
        original,
    )

    assert result.triggered is True
    assert result.reason is FaultApplicationReason.APPLIED
    assert result.response.status_code == 500
    assert result.response.body == original.body


def test_removes_nested_json_value() -> None:
    result = apply_response_fault(
        definition({"type": "remove_json_value", "pointer": "/items/0/id"}),
        request(),
        response(),
    )

    assert result.triggered is True
    assert decoded(result.response.body) == {
        "id": 1,
        "name": "pencil",
        "items": [{}],
    }


def test_replaces_json_value_with_different_type() -> None:
    result = apply_response_fault(
        definition({"type": "replace_json_value", "pointer": "/id", "value": "wrong"}),
        request(),
        response(),
    )

    assert result.triggered is True
    assert decoded(result.response.body)["id"] == "wrong"  # type: ignore[index]


def test_treats_boolean_as_different_from_json_number() -> None:
    result = apply_response_fault(
        definition({"type": "replace_json_value", "pointer": "/id", "value": True}),
        request(),
        response(),
    )

    assert result.triggered is True
    assert decoded(result.response.body)["id"] is True  # type: ignore[index]


def test_replaces_complete_json_document() -> None:
    result = apply_response_fault(
        definition({"type": "replace_json_value", "pointer": "", "value": []}),
        request(),
        response(),
    )

    assert result.triggered is True
    assert decoded(result.response.body) == []


def test_duplicates_json_array_item() -> None:
    result = apply_response_fault(
        definition({"type": "duplicate_json_array_item", "pointer": "/items", "index": 0}),
        request(),
        response(),
    )

    assert result.triggered is True
    assert decoded(result.response.body)["items"] == [{"id": 1}, {"id": 1}]  # type: ignore[index]


def test_body_mutation_updates_content_length() -> None:
    result = apply_response_fault(
        definition({"type": "remove_json_value", "pointer": "/name"}),
        request(),
        response(),
    )

    content_length = dict(result.response.headers)["content-length"]
    assert content_length == str(len(result.response.body))


@pytest.mark.parametrize(
    ("fault", "fault_request", "fault_response", "reason"),
    [
        (
            definition({"type": "replace_status", "status_code": 500}),
            request(method="POST"),
            response(),
            FaultApplicationReason.METHOD_MISMATCH,
        ),
        (
            definition({"type": "replace_status", "status_code": 500}),
            request(path="/items"),
            response(),
            FaultApplicationReason.PATH_MISMATCH,
        ),
        (
            definition({"type": "replace_status", "status_code": 500}),
            request(),
            response(status_code=201),
            FaultApplicationReason.STATUS_MISMATCH,
        ),
        (
            definition({"type": "replace_status", "status_code": 500}),
            request(),
            response(media_type="text/plain"),
            FaultApplicationReason.MEDIA_TYPE_MISMATCH,
        ),
    ],
)
def test_does_not_trigger_when_matcher_rejects_response(
    fault: FaultDefinition,
    fault_request: FaultRequestContext,
    fault_response: FaultResponse,
    reason: FaultApplicationReason,
) -> None:
    result = apply_response_fault(fault, fault_request, fault_response)

    assert result.triggered is False
    assert result.reason is reason
    assert result.response is fault_response


def test_does_not_trigger_for_missing_pointer() -> None:
    original = response()

    result = apply_response_fault(
        definition({"type": "remove_json_value", "pointer": "/missing"}),
        request(),
        original,
    )

    assert result.triggered is False
    assert result.reason is FaultApplicationReason.POINTER_NOT_FOUND
    assert result.response is original


def test_does_not_trigger_when_duplicate_target_is_not_an_array() -> None:
    result = apply_response_fault(
        definition({"type": "duplicate_json_array_item", "pointer": "/name", "index": 0}),
        request(),
        response(),
    )

    assert result.triggered is False
    assert result.reason is FaultApplicationReason.TARGET_NOT_ARRAY


def test_does_not_trigger_when_duplicate_index_is_out_of_range() -> None:
    result = apply_response_fault(
        definition({"type": "duplicate_json_array_item", "pointer": "/items", "index": 2}),
        request(),
        response(),
    )

    assert result.triggered is False
    assert result.reason is FaultApplicationReason.INDEX_OUT_OF_RANGE


def test_does_not_trigger_for_invalid_json() -> None:
    invalid_response = FaultResponse(
        status_code=200,
        headers=(("content-type", "application/json"),),
        body=b"not-json",
    )

    result = apply_response_fault(
        definition({"type": "remove_json_value", "pointer": "/name"}),
        request(),
        invalid_response,
    )

    assert result.triggered is False
    assert result.reason is FaultApplicationReason.INVALID_JSON


def test_does_not_trigger_json_mutation_for_text_response() -> None:
    text_response = FaultResponse(
        status_code=200,
        headers=(("content-type", "text/plain"),),
        body=b"hello",
    )

    result = apply_response_fault(
        definition(
            {"type": "remove_json_value", "pointer": "/name"},
            media_type=None,
        ),
        request(),
        text_response,
    )

    assert result.triggered is False
    assert result.reason is FaultApplicationReason.RESPONSE_NOT_JSON


def test_does_not_trigger_when_replacement_is_identical() -> None:
    result = apply_response_fault(
        definition({"type": "replace_json_value", "pointer": "/id", "value": 1}),
        request(),
        response(),
    )

    assert result.triggered is False
    assert result.reason is FaultApplicationReason.NO_CHANGE


def test_supports_escaped_json_pointer_tokens() -> None:
    result = apply_response_fault(
        definition({"type": "remove_json_value", "pointer": "/a~1b/~0key"}),
        request(),
        response({"a/b": {"~key": 1}}),
    )

    assert result.triggered is True
    assert decoded(result.response.body) == {"a/b": {}}
