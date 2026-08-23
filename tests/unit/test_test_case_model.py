import re

import pytest
from pydantic import ValidationError

from openapi_ai_test_evaluator.domain import ExecutionConfig
from openapi_ai_test_evaluator.domain import TestCase as CaseModel
from openapi_ai_test_evaluator.domain import TestCaseBatch as CaseBatchModel
from openapi_ai_test_evaluator.domain.test_plan import Scenario


def minimal_case(case_id: str = "read-item") -> dict[str, object]:
    return {
        "id": case_id,
        "steps": [{"id": "read", "operation_id": "getItem"}],
    }


def test_accepts_one_runner_ready_case() -> None:
    batch = CaseBatchModel.model_validate(
        {
            "schema_version": "1.0",
            "cases": [minimal_case()],
        }
    )

    assert batch.cases == [CaseModel.model_validate(minimal_case())]


def test_test_case_is_independent_from_legacy_scenario() -> None:
    assert not issubclass(CaseModel, Scenario)


def test_rejects_an_empty_batch() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        CaseBatchModel.model_validate({"schema_version": "1.0", "cases": []})


def test_rejects_duplicate_case_ids() -> None:
    with pytest.raises(ValidationError, match="case IDs must be unique"):
        CaseBatchModel.model_validate(
            {
                "schema_version": "1.0",
                "cases": [minimal_case(), minimal_case()],
            }
        )


def test_rejects_batch_metadata_owned_by_other_layers() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CaseBatchModel.model_validate(
            {
                "schema_version": "1.0",
                "cases": [minimal_case()],
                "provider": "deepseek",
            }
        )


def test_batch_schema_has_only_execution_content() -> None:
    schema = CaseBatchModel.model_json_schema()

    assert set(schema["required"]) == {"schema_version", "cases"}
    assert "TestCase" in schema["$defs"]


def test_execution_config_keeps_runtime_settings_outside_generated_cases() -> None:
    config = ExecutionConfig.model_validate(
        {
            "timeout_ms": 1500,
            "headers": {"Authorization": "Bearer test"},
            "initial_variables": {"item_id": "item-1"},
        }
    )

    assert config.timeout_ms == 1500
    assert config.initial_variables == {"item_id": "item-1"}


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("timeout_ms", 0, "greater than or equal to 1"),
        ("initial_variables", {"": "item-1"}, "variable names cannot be empty"),
        (
            "initial_variables",
            {"item_id": {"$var": "source", "extra": True}},
            "a $var reference cannot contain sibling keys",
        ),
    ],
)
def test_execution_config_rejects_invalid_runtime_settings(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        ExecutionConfig.model_validate({field: value})
