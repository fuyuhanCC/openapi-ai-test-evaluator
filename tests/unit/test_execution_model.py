from copy import deepcopy

import pytest
from pydantic import ValidationError

from openapi_ai_test_evaluator.domain import RunResult
from openapi_ai_test_evaluator.domain.execution import ExtractionResult


def empty_body() -> dict[str, object]:
    return {
        "media_type": None,
        "value": None,
        "size_bytes": 0,
        "truncated": False,
    }


def minimal_result() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "kind": "RunResult",
        "run_id": "run-20260820-001",
        "plan_name": "minimal-get",
        "spec_id": "demo-items-v1",
        "started_at": "2026-08-20T10:00:00+08:00",
        "finished_at": "2026-08-20T10:00:00.050+08:00",
        "duration_ms": 50,
        "outcome": "passed",
        "fault": {
            "configured_fault_id": None,
            "trigger_status": "not_configured",
            "trigger_count": 0,
        },
        "scenarios": [
            {
                "scenario_id": "read-item",
                "outcome": "passed",
                "steps": [
                    {
                        "phase": "main",
                        "step_id": "read",
                        "operation_id": "getItem",
                        "outcome_policy": "required",
                        "outcome": "passed",
                        "duration_ms": 50,
                        "retry_count": 0,
                        "request": {
                            "method": "GET",
                            "path": "/items/1",
                            "query": [],
                            "headers": {},
                            "body": empty_body(),
                        },
                        "response": {
                            "status_code": 200,
                            "headers": {"content-type": "application/json"},
                            "body": {
                                "media_type": "application/json",
                                "value": {"id": 1},
                                "size_bytes": 8,
                                "truncated": False,
                            },
                        },
                        "extractions": [],
                        "assertions": [],
                        "errors": [],
                    }
                ],
                "relations": [],
                "errors": [],
            }
        ],
        "errors": [],
    }


def first_step(raw_result: dict[str, object]) -> dict[str, object]:
    scenarios = raw_result["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[0]
    assert isinstance(scenario, dict)
    steps = scenario["steps"]
    assert isinstance(steps, list)
    step = steps[0]
    assert isinstance(step, dict)
    return step


def test_accepts_minimal_run_result() -> None:
    result = RunResult.model_validate(minimal_result())

    assert result.run_id == "run-20260820-001"
    assert result.scenarios[0].steps[0].response is not None
    assert result.scenarios[0].steps[0].response.status_code == 200


def test_rejects_unknown_fields() -> None:
    raw_result = minimal_result()
    raw_result["unexpected"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RunResult.model_validate(raw_result)


def test_rejects_finish_before_start() -> None:
    raw_result = minimal_result()
    raw_result["finished_at"] = "2026-08-20T09:59:59+08:00"

    with pytest.raises(ValidationError, match="finished_at cannot precede started_at"):
        RunResult.model_validate(raw_result)


def test_rejects_timestamps_without_timezone() -> None:
    raw_result = minimal_result()
    raw_result["started_at"] = "2026-08-20T10:00:00"

    with pytest.raises(ValidationError, match="timestamps must include a timezone"):
        RunResult.model_validate(raw_result)


def test_rejects_nonzero_retry_count_in_v1() -> None:
    raw_result = minimal_result()
    first_step(raw_result)["retry_count"] = 1

    with pytest.raises(ValidationError, match="Input should be 0"):
        RunResult.model_validate(raw_result)


def test_rejects_best_effort_outside_cleanup() -> None:
    raw_result = minimal_result()
    first_step(raw_result)["outcome_policy"] = "best_effort"

    with pytest.raises(ValidationError, match="best_effort is valid only for cleanup"):
        RunResult.model_validate(raw_result)


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        (
            {
                "configured_fault_id": "wrong-status",
                "trigger_status": "not_configured",
                "trigger_count": 0,
            },
            "not_configured requires no configured fault",
        ),
        (
            {
                "configured_fault_id": None,
                "trigger_status": "triggered",
                "trigger_count": 1,
            },
            "configured fault status requires configured_fault_id",
        ),
        (
            {
                "configured_fault_id": "wrong-status",
                "trigger_status": "triggered",
                "trigger_count": 0,
            },
            "triggered faults require a positive trigger_count",
        ),
    ],
)
def test_rejects_inconsistent_fault_observations(fault: dict[str, object], message: str) -> None:
    raw_result = minimal_result()
    raw_result["fault"] = fault

    with pytest.raises(ValidationError, match=message):
        RunResult.model_validate(raw_result)


def test_rejects_relation_kind_that_does_not_match_type() -> None:
    raw_result = minimal_result()
    scenarios = raw_result["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[0]
    assert isinstance(scenario, dict)
    scenario["relations"] = [
        {
            "relation_id": "read-consistency",
            "kind": "lifecycle",
            "type": "repeated_read_consistency",
            "source_step": "read",
            "follow_up_step": "read",
            "baseline_step": None,
            "outcome": "not_applicable",
            "comparisons": [],
            "errors": [],
        }
    ]

    with pytest.raises(ValidationError, match="relation kind .* does not match type"):
        RunResult.model_validate(raw_result)


def test_not_applicable_relation_requires_an_explanation() -> None:
    raw_result = minimal_result()
    scenarios = raw_result["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[0]
    assert isinstance(scenario, dict)
    scenario["relations"] = [
        {
            "relation_id": "read-consistency",
            "kind": "metamorphic",
            "type": "repeated_read_consistency",
            "source_step": "read",
            "follow_up_step": "read",
            "baseline_step": None,
            "outcome": "not_applicable",
            "comparisons": [],
            "errors": [],
        }
    ]

    with pytest.raises(ValidationError, match="require a message"):
        RunResult.model_validate(raw_result)


def test_rejects_duplicate_step_ids() -> None:
    raw_result = minimal_result()
    scenarios = raw_result["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[0]
    assert isinstance(scenario, dict)
    steps = scenario["steps"]
    assert isinstance(steps, list)
    steps.append(deepcopy(steps[0]))

    with pytest.raises(ValidationError, match="step IDs must be unique"):
        RunResult.model_validate(raw_result)


def test_one_of_comparison_requires_expected_values() -> None:
    raw_result = minimal_result()
    scenarios = raw_result["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[0]
    assert isinstance(scenario, dict)
    scenario["relations"] = [
        {
            "relation_id": "deleted-item-unavailable",
            "kind": "lifecycle",
            "type": "delete_read_consistency",
            "source_step": "read",
            "follow_up_step": "read",
            "baseline_step": None,
            "outcome": "passed",
            "comparisons": [
                {
                    "comparison_id": "accepted-status",
                    "operator": "one_of",
                    "outcome": "passed",
                    "source": {
                        "step_id": "read",
                        "location": "response.status",
                        "pointer": None,
                        "value": 204,
                    },
                    "follow_up": {
                        "step_id": "read",
                        "location": "response.status",
                        "pointer": None,
                        "value": 404,
                    },
                    "expected": None,
                    "message": None,
                }
            ],
            "errors": [],
        }
    ]

    with pytest.raises(ValidationError, match="one_of comparisons require"):
        RunResult.model_validate(raw_result)


def test_schema_contains_top_level_contract_and_error_categories() -> None:
    schema = RunResult.model_json_schema()

    assert schema["title"] == "RunResult"
    assert set(schema["required"]) == {
        "schema_version",
        "kind",
        "run_id",
        "plan_name",
        "spec_id",
        "started_at",
        "finished_at",
        "duration_ms",
        "outcome",
        "fault",
        "scenarios",
    }
    assert "runner_internal_error" in schema["$defs"]["ErrorCategory"]["enum"]


def test_accepts_partially_redacted_nested_extraction_value() -> None:
    result = ExtractionResult.model_validate(
        {
            "variable": "item",
            "source": "response.body",
            "pointer": "",
            "required": True,
            "status": "extracted",
            "value": {"id": 7, "credentials": {"token": "[REDACTED]"}},
            "redacted": True,
        }
    )

    assert result.redacted is True


def test_rejects_redacted_flag_without_an_extracted_value() -> None:
    with pytest.raises(ValidationError, match="only extracted values may be marked as redacted"):
        ExtractionResult.model_validate(
            {
                "variable": "item",
                "source": "response.body",
                "pointer": "/id",
                "required": True,
                "status": "missing",
                "value": None,
                "redacted": True,
            }
        )
