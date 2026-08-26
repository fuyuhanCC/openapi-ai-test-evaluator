from copy import deepcopy

import pytest
from pydantic import ValidationError

from openapi_ai_test_evaluator.domain import AdaptationRecord


def adaptation_record() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "kind": "AdaptationRecord",
        "tool": "schemathesis",
        "tool_version": "4.25.2",
        "adapter_version": "schemathesis-case-v1",
        "seed": 7,
        "received_case_count": 3,
        "adapted_case_count": 2,
        "rejected_case_count": 1,
        "skip_reasons": [
            {
                "code": "media_type_unsupported",
                "detail_code": None,
                "count": 1,
            }
        ],
    }


def test_accepts_consistent_adaptation_record() -> None:
    record = AdaptationRecord.model_validate(adaptation_record())

    assert record.tool == "schemathesis"
    assert record.received_case_count == 3
    assert record.skip_reasons[0].count == 1


def test_rejects_inconsistent_case_counts() -> None:
    raw = adaptation_record()
    raw["received_case_count"] = 4

    with pytest.raises(ValidationError, match="received cases must equal"):
        AdaptationRecord.model_validate(raw)


def test_rejects_skip_counts_that_do_not_cover_rejected_cases() -> None:
    raw = adaptation_record()
    reasons = raw["skip_reasons"]
    assert isinstance(reasons, list)
    reason = reasons[0]
    assert isinstance(reason, dict)
    reason["count"] = 2

    with pytest.raises(ValidationError, match="skip reason counts must equal"):
        AdaptationRecord.model_validate(raw)


def test_rejects_duplicate_skip_reason_keys() -> None:
    raw = adaptation_record()
    reasons = raw["skip_reasons"]
    assert isinstance(reasons, list)
    reasons.append(deepcopy(reasons[0]))
    raw["rejected_case_count"] = 2
    raw["received_case_count"] = 4

    with pytest.raises(ValidationError, match="skip reasons must be unique"):
        AdaptationRecord.model_validate(raw)
