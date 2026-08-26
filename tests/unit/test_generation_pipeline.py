import json
from pathlib import Path

from openapi_ai_test_evaluator.domain import GenerationConfig
from openapi_ai_test_evaluator.domain.generation import GenerationStatus
from openapi_ai_test_evaluator.generation import (
    PROMPT_VERSION,
    FakeProvider,
    ProviderResponse,
    build_provider_request,
    generate_cases_from_openapi,
)
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]
DEMO_SPEC = ROOT / "examples" / "demo-items" / "openapi.yaml"


def config(**overrides: object) -> GenerationConfig:
    values = {
        "model": "deepseek-v4-flash",
        "prompt_version": PROMPT_VERSION,
        "max_cases": 5,
        "max_steps_per_case": 3,
        **overrides,
    }
    return GenerationConfig.model_validate(values)


def output(cases: list[dict[str, object]]) -> str:
    return json.dumps({"schema_version": "1.0", "cases": cases})


def response(output_text: str) -> ProviderResponse:
    return ProviderResponse(
        output_text=output_text,
        model="deepseek-v4-flash",
        request_id="request-001",
        finish_reason="stop",
        token_usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )


def list_case(case_id: str = "list-items") -> dict[str, object]:
    return {
        "id": case_id,
        "steps": [
            {
                "id": "list",
                "operation_id": "listItems",
                "assertions": [
                    {"operator": "status_is", "expected": 200},
                    {"operator": "schema_matches"},
                ],
            }
        ],
    }


def test_runs_complete_generation_pipeline_with_fake_provider() -> None:
    spec = load_openapi(DEMO_SPEC)
    generation_config = config(seed=7)
    provider = FakeProvider(response=response(output([list_case()])), name="deepseek")

    attempt = generate_cases_from_openapi(
        provider,
        spec,
        generation_config,
        generation_id="generation-001",
    )

    assert attempt.batch is not None
    assert attempt.batch.cases[0].id == "list-items"
    assert attempt.record.status is GenerationStatus.SUCCEEDED
    assert attempt.record.prompt_version == PROMPT_VERSION
    assert attempt.record.token_usage.total_tokens == 120
    assert attempt.record.case_admission is not None
    assert attempt.record.case_admission.received_case_count == 1
    assert attempt.record.case_admission.admitted_case_count == 1
    assert attempt.provider_output_text == output([list_case()])
    assert provider.requests == (build_provider_request(spec, generation_config),)


def test_admits_cases_within_limit_and_records_cases_beyond_it() -> None:
    provider = FakeProvider(
        response=response(output([list_case("first-case"), list_case("second-case")]))
    )

    attempt = generate_cases_from_openapi(
        provider,
        load_openapi(DEMO_SPEC),
        config(max_cases=1),
        generation_id="generation-001",
    )

    assert attempt.batch is not None
    assert [case.id for case in attempt.batch.cases] == ["first-case"]
    assert attempt.record.status is GenerationStatus.SUCCEEDED
    assert attempt.record.error is None
    assert attempt.record.case_admission is not None
    assert attempt.record.case_admission.rejected_case_count == 1
    assert attempt.record.case_admission.rejections[0].code == "case_count_limit_exceeded"
    assert attempt.provider_output_text is not None


def test_rejects_case_with_too_many_total_steps() -> None:
    case = list_case()
    case["setup"] = [{"id": "prepare", "operation_id": "listItems"}]
    case["cleanup"] = [{"id": "finish", "operation_id": "listItems"}]
    provider = FakeProvider(response=response(output([case])))

    attempt = generate_cases_from_openapi(
        provider,
        load_openapi(DEMO_SPEC),
        config(max_steps_per_case=2),
        generation_id="generation-001",
    )

    assert attempt.batch is None
    assert attempt.record.error is not None
    assert attempt.record.error.code == "no-admitted-test-cases"
    assert attempt.record.case_admission is not None
    assert attempt.record.case_admission.rejections[0].code == "case_step_limit_exceeded"
    assert attempt.provider_output_text == output([case])


def test_rejects_structurally_valid_cases_that_invent_operations() -> None:
    invented_case = {
        "id": "invented-operation",
        "steps": [{"id": "call", "operation_id": "deleteEverything"}],
    }
    provider = FakeProvider(response=response(output([invented_case])))

    attempt = generate_cases_from_openapi(
        provider,
        load_openapi(DEMO_SPEC),
        config(),
        generation_id="generation-001",
    )

    assert attempt.batch is None
    assert attempt.record.status is GenerationStatus.INVALID_OUTPUT
    assert attempt.record.error is not None
    assert attempt.record.error.code == "no-admitted-test-cases"
    assert attempt.record.case_admission is not None
    assert attempt.record.case_admission.rejections[0].code == "case_semantics_invalid"
    assert attempt.record.case_admission.rejections[0].detail_codes == ["unknown_operation"]
    assert attempt.provider_output_text == output([invented_case])


def test_keeps_valid_cases_when_other_cases_fail_structure_and_semantics() -> None:
    cases = [
        list_case("valid-case"),
        {"id": "missing-steps"},
        {
            "id": "unknown-operation",
            "steps": [{"id": "call", "operation_id": "inventedOperation"}],
        },
    ]
    provider = FakeProvider(response=response(output(cases)))

    attempt = generate_cases_from_openapi(
        provider,
        load_openapi(DEMO_SPEC),
        config(),
        generation_id="generation-001",
    )

    assert attempt.batch is not None
    assert [case.id for case in attempt.batch.cases] == ["valid-case"]
    assert attempt.record.status is GenerationStatus.SUCCEEDED
    assert attempt.record.case_admission is not None
    assert attempt.record.case_admission.received_case_count == 3
    assert attempt.record.case_admission.admitted_case_count == 1
    assert attempt.record.case_admission.rejected_case_count == 2
