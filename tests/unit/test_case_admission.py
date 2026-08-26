import json
from pathlib import Path

import pytest

from openapi_ai_test_evaluator.domain import GenerationConfig
from openapi_ai_test_evaluator.domain.generation import CaseAdmissionStage
from openapi_ai_test_evaluator.generation import (
    ProviderOutputAdmissionError,
    admit_generated_cases,
)
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")
CONFIG = GenerationConfig(
    model="deepseek-v4-flash",
    prompt_version="api-cases-v3",
    max_cases=5,
    max_steps_per_case=3,
)


def case(case_id: str, operation_id: str = "listItems") -> dict[str, object]:
    return {
        "id": case_id,
        "steps": [{"id": "request", "operation_id": operation_id}],
    }


def output(cases: list[object]) -> str:
    return json.dumps({"schema_version": "1.0", "cases": cases})


def test_admits_valid_cases_and_records_each_rejected_case() -> None:
    admission = admit_generated_cases(
        output(
            [
                case("valid-case"),
                {"id": "missing-steps"},
                case("unknown-operation", "inventedOperation"),
            ]
        ),
        SPEC,
        CONFIG,
    )

    assert admission.batch is not None
    assert [item.id for item in admission.batch.cases] == ["valid-case"]
    assert admission.summary.received_case_count == 3
    assert admission.summary.admitted_case_count == 1
    assert admission.summary.rejected_case_count == 2
    assert [rejection.stage for rejection in admission.summary.rejections] == [
        CaseAdmissionStage.STRUCTURE,
        CaseAdmissionStage.SEMANTIC,
    ]
    assert [rejection.code for rejection in admission.summary.rejections] == [
        "case_structure_invalid",
        "case_semantics_invalid",
    ]
    assert admission.summary.rejections[1].detail_codes == ["unknown_operation"]


def test_rejects_only_cases_beyond_the_configured_case_limit() -> None:
    admission = admit_generated_cases(
        output([case("first"), case("second")]),
        SPEC,
        CONFIG.model_copy(update={"max_cases": 1}),
    )

    assert admission.batch is not None
    assert [item.id for item in admission.batch.cases] == ["first"]
    assert admission.summary.rejections[0].stage is CaseAdmissionStage.LIMIT
    assert admission.summary.rejections[0].code == "case_count_limit_exceeded"


def test_rejects_duplicate_ids_and_step_limit_per_case() -> None:
    too_many_steps = case("too-many-steps")
    too_many_steps["setup"] = [{"id": "setup", "operation_id": "listItems"}]
    too_many_steps["cleanup"] = [{"id": "cleanup", "operation_id": "listItems"}]
    admission = admit_generated_cases(
        output([case("same-id"), case("same-id"), too_many_steps]),
        SPEC,
        CONFIG.model_copy(update={"max_steps_per_case": 2}),
    )

    assert admission.batch is not None
    assert admission.summary.admitted_case_count == 1
    assert [rejection.code for rejection in admission.summary.rejections] == [
        "duplicate_case_id",
        "case_step_limit_exceeded",
    ]


def test_returns_no_batch_when_no_case_is_admitted() -> None:
    admission = admit_generated_cases(
        output([{"id": "missing-steps"}]),
        SPEC,
        CONFIG,
    )

    assert admission.batch is None
    assert admission.summary.admitted_case_count == 0
    assert admission.summary.rejected_case_count == 1


@pytest.mark.parametrize(
    "raw_output",
    [
        "not-json",
        json.dumps([]),
        json.dumps({"schema_version": "2.0", "cases": []}),
        json.dumps({"schema_version": "1.0", "cases": [], "extra": True}),
        json.dumps({"schema_version": "1.0", "cases": {}}),
    ],
)
def test_rejects_undecodable_batch_envelopes(raw_output: str) -> None:
    with pytest.raises(ProviderOutputAdmissionError):
        admit_generated_cases(raw_output, SPEC, CONFIG)
