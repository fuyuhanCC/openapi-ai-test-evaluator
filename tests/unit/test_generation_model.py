from copy import deepcopy

import pytest
from pydantic import ValidationError

from openapi_ai_test_evaluator.domain import GenerationRecord


def successful_record() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "kind": "GenerationRecord",
        "generation_id": "generation-001",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "prompt_version": "api-cases-v1",
        "started_at": "2026-08-23T10:00:00+08:00",
        "finished_at": "2026-08-23T10:00:01+08:00",
        "duration_ms": 1000,
        "status": "succeeded",
        "request_count": 1,
        "token_usage": {
            "input_tokens": 1200,
            "output_tokens": 400,
            "total_tokens": 1600,
            "cached_input_tokens": 0,
            "reasoning_tokens": None,
        },
        "estimated_cost_usd": 0.0008,
        "error": None,
    }


def test_accepts_successful_llm_generation_record() -> None:
    record = GenerationRecord.model_validate(successful_record())

    assert record.provider == "deepseek"
    assert record.token_usage.total_tokens == 1600
    assert record.estimated_cost_usd == 0.0008


def test_accepts_per_case_admission_metrics() -> None:
    raw = successful_record()
    raw["case_admission"] = {
        "received_case_count": 3,
        "admitted_case_count": 2,
        "rejected_case_count": 1,
        "rejections": [
            {
                "index": 1,
                "case_id": "bad-case",
                "stage": "semantic",
                "code": "case_semantics_invalid",
                "detail_codes": ["unknown_operation"],
            }
        ],
    }

    record = GenerationRecord.model_validate(raw)

    assert record.case_admission is not None
    assert record.case_admission.admitted_case_count == 2
    assert record.case_admission.rejections[0].detail_codes == ["unknown_operation"]


@pytest.mark.parametrize(
    "changes",
    [
        {"received_case_count": 4},
        {"rejected_case_count": 2},
        {
            "rejections": [
                {
                    "index": 3,
                    "stage": "structure",
                    "code": "case_structure_invalid",
                }
            ]
        },
    ],
)
def test_rejects_inconsistent_case_admission_metrics(changes: dict[str, object]) -> None:
    raw = successful_record()
    admission: dict[str, object] = {
        "received_case_count": 3,
        "admitted_case_count": 2,
        "rejected_case_count": 1,
        "rejections": [
            {
                "index": 1,
                "stage": "structure",
                "code": "case_structure_invalid",
            }
        ],
    }
    admission.update(changes)
    raw["case_admission"] = admission

    with pytest.raises(ValidationError):
        GenerationRecord.model_validate(raw)


def test_rejects_success_record_with_zero_admitted_cases() -> None:
    raw = successful_record()
    raw["case_admission"] = {
        "received_case_count": 1,
        "admitted_case_count": 0,
        "rejected_case_count": 1,
        "rejections": [
            {
                "index": 0,
                "stage": "structure",
                "code": "case_structure_invalid",
            }
        ],
    }

    with pytest.raises(ValidationError, match="require an admitted case"):
        GenerationRecord.model_validate(raw)


def test_accepts_tool_baseline_without_model_or_tokens() -> None:
    raw = successful_record()
    raw.update(
        {
            "provider": "schemathesis",
            "model": None,
            "prompt_version": None,
            "request_count": 0,
            "token_usage": {},
            "estimated_cost_usd": 0,
        }
    )

    record = GenerationRecord.model_validate(raw)

    assert record.model is None
    assert record.token_usage.input_tokens is None


def test_accepts_failed_attempt_with_sanitized_error() -> None:
    raw = successful_record()
    raw.update(
        {
            "status": "invalid_output",
            "error": {
                "code": "invalid-json",
                "message": "provider output was not valid JSON",
                "retryable": True,
            },
        }
    )

    record = GenerationRecord.model_validate(raw)

    assert record.error is not None
    assert record.error.code == "invalid-json"


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "total_tokens"])
def test_rejects_negative_token_counts(field: str) -> None:
    raw = successful_record()
    token_usage = raw["token_usage"]
    assert isinstance(token_usage, dict)
    token_usage[field] = -1

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        GenerationRecord.model_validate(raw)


def test_rejects_negative_estimated_cost() -> None:
    raw = successful_record()
    raw["estimated_cost_usd"] = -0.01

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        GenerationRecord.model_validate(raw)


def test_rejects_non_finite_estimated_cost() -> None:
    raw = successful_record()
    raw["estimated_cost_usd"] = float("inf")

    with pytest.raises(ValidationError, match="finite number"):
        GenerationRecord.model_validate(raw)


def test_rejects_naive_timestamps() -> None:
    raw = successful_record()
    raw["started_at"] = "2026-08-23T10:00:00"

    with pytest.raises(ValidationError, match="timestamps must include a timezone"):
        GenerationRecord.model_validate(raw)


def test_rejects_finish_before_start() -> None:
    raw = successful_record()
    raw["finished_at"] = "2026-08-23T09:59:59+08:00"

    with pytest.raises(ValidationError, match="finished_at cannot precede started_at"):
        GenerationRecord.model_validate(raw)


def test_failed_generation_requires_structured_error() -> None:
    raw = successful_record()
    raw["status"] = "provider_error"

    with pytest.raises(ValidationError, match="unsuccessful generation records require an error"):
        GenerationRecord.model_validate(raw)


def test_successful_generation_rejects_error() -> None:
    raw = successful_record()
    raw["error"] = {
        "code": "rate-limited",
        "message": "provider rejected the request",
        "retryable": True,
    }

    with pytest.raises(ValidationError, match="successful generation records cannot contain"):
        GenerationRecord.model_validate(raw)


def test_schema_keeps_generated_cases_outside_generation_metadata() -> None:
    schema = GenerationRecord.model_json_schema()

    assert "cases" not in schema["properties"]
    assert "provider" in schema["required"]
    assert "token_usage" not in schema["required"]


def test_rejects_unknown_provider_specific_fields() -> None:
    raw = deepcopy(successful_record())
    raw["deepseek_cache_hit"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GenerationRecord.model_validate(raw)
