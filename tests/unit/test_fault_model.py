import pytest
from pydantic import ValidationError

from openapi_ai_test_evaluator.domain.fault import FaultDefinition, FaultProxyState


def fault_definition(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "1.0",
        "fault_id": "wrong-item-id",
        "description": "Return a different item identifier.",
        "category": "response_body",
        "matcher": {
            "method": "GET",
            "path_regex": r"^/items/[0-9]+$",
            "response_statuses": [200],
            "response_media_type": "application/json",
        },
        "mutation": {
            "type": "replace_json_value",
            "pointer": "/id",
            "value": -1,
        },
    }
    value.update(overrides)
    return value


def test_accepts_response_body_fault_definition() -> None:
    fault = FaultDefinition.model_validate(fault_definition())

    assert fault.fault_id == "wrong-item-id"
    assert fault.matcher.method == "GET"
    assert fault.mutation.type == "replace_json_value"


def test_accepts_status_fault_definition() -> None:
    fault = FaultDefinition.model_validate(
        fault_definition(
            category="status",
            mutation={"type": "replace_status", "status_code": 500},
        )
    )

    assert fault.mutation.type == "replace_status"


@pytest.mark.parametrize(
    ("path_regex", "message"),
    [
        (r"/items/[0-9]+$", "anchored at the start"),
        (r"^/items/($", "path_regex is invalid"),
    ],
)
def test_rejects_invalid_matcher_regex(path_regex: str, message: str) -> None:
    value = fault_definition()
    assert isinstance(value["matcher"], dict)
    value["matcher"]["path_regex"] = path_regex

    with pytest.raises(ValidationError, match=message):
        FaultDefinition.model_validate(value)


def test_rejects_duplicate_response_statuses() -> None:
    value = fault_definition()
    assert isinstance(value["matcher"], dict)
    value["matcher"]["response_statuses"] = [200, 200]

    with pytest.raises(ValidationError, match="must not contain duplicates"):
        FaultDefinition.model_validate(value)


def test_rejects_category_that_disagrees_with_mutation() -> None:
    with pytest.raises(ValidationError, match="does not match mutation"):
        FaultDefinition.model_validate(
            fault_definition(
                category="status",
                mutation={
                    "type": "remove_json_value",
                    "pointer": "/name",
                },
            )
        )


def test_rejects_removing_json_document_root() -> None:
    with pytest.raises(ValidationError, match="cannot remove the document root"):
        FaultDefinition.model_validate(
            fault_definition(
                mutation={
                    "type": "remove_json_value",
                    "pointer": "",
                }
            )
        )


def test_accepts_active_fault_that_has_not_triggered() -> None:
    state = FaultProxyState.model_validate(
        {
            "mode": "active",
            "configured_fault_id": "wrong-item-id",
            "trigger_count": 0,
        }
    )

    assert state.trigger_count == 0


@pytest.mark.parametrize(
    "value",
    [
        {
            "mode": "pass_through",
            "configured_fault_id": "wrong-item-id",
            "trigger_count": 0,
        },
        {
            "mode": "pass_through",
            "configured_fault_id": None,
            "trigger_count": 1,
        },
        {
            "mode": "active",
            "configured_fault_id": None,
            "trigger_count": 0,
        },
    ],
)
def test_rejects_inconsistent_proxy_state(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FaultProxyState.model_validate(value)
