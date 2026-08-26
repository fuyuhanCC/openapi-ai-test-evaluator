from pathlib import Path

import pytest
import schemathesis
from pydantic import ValidationError

from openapi_ai_test_evaluator.generation import (
    SchemathesisGenerationConfig,
    generate_schemathesis_batch,
)
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]
SPEC_PATH = ROOT / "examples" / "demo-items" / "openapi.yaml"
SPEC = load_openapi(SPEC_PATH)


def test_generates_all_coverage_and_per_operation_fuzzing_without_http() -> None:
    schema = schemathesis.openapi.from_path(SPEC_PATH)

    result = generate_schemathesis_batch(
        schema,
        SPEC,
        SchemathesisGenerationConfig(
            include_examples=False,
            include_coverage=True,
            fuzzing_positive_cases_per_operation=2,
            fuzzing_negative_cases_per_operation=1,
            seed=7,
        ),
    )

    # The pinned Schemathesis version emits 236 finite coverage cases for this
    # six-operation fixture, followed by 3 fuzzing cases per operation.
    assert result.record.received_case_count == 236 + (6 * 3)
    assert (
        result.record.adapted_case_count + result.record.rejected_case_count
        == result.record.received_case_count
    )
    assert result.record.seed == 7
    if result.batch is not None:
        phases = {
            tag
            for case in result.batch.cases
            for tag in case.tags
            if tag.startswith("phase:")
        }
        assert phases == {"phase:coverage", "phase:fuzzing"}


def test_generation_is_reproducible_for_the_same_seed() -> None:
    config = SchemathesisGenerationConfig(
        include_examples=False,
        include_coverage=False,
        fuzzing_positive_cases_per_operation=2,
        fuzzing_negative_cases_per_operation=1,
        seed=19,
    )

    first = generate_schemathesis_batch(
        schemathesis.openapi.from_path(SPEC_PATH), SPEC, config
    )
    second = generate_schemathesis_batch(
        schemathesis.openapi.from_path(SPEC_PATH), SPEC, config
    )

    first_batch = first.batch.model_dump(mode="json") if first.batch is not None else None
    second_batch = second.batch.model_dump(mode="json") if second.batch is not None else None
    assert second_batch == first_batch
    assert second.record == first.record


def test_fuzzing_count_applies_to_every_openapi_operation() -> None:
    result = generate_schemathesis_batch(
        schemathesis.openapi.from_path(SPEC_PATH),
        SPEC,
        SchemathesisGenerationConfig(
            include_examples=False,
            include_coverage=False,
            fuzzing_positive_cases_per_operation=2,
            fuzzing_negative_cases_per_operation=0,
            seed=3,
        ),
    )

    assert result.record.received_case_count == 12
    if result.batch is not None:
        assert all("mode:positive" in case.tags for case in result.batch.cases)
        assert {case.steps[0].operation_id for case in result.batch.cases} == set(SPEC.operations)


def test_collects_explicit_openapi_examples() -> None:
    raw_schema = schemathesis.openapi.from_path(SPEC_PATH).raw_schema
    raw_schema["paths"]["/items"]["get"]["parameters"][3]["example"] = 7
    schema = schemathesis.openapi.from_dict(raw_schema)

    result = generate_schemathesis_batch(
        schema,
        SPEC,
        SchemathesisGenerationConfig(
            include_examples=True,
            include_coverage=False,
            fuzzing_positive_cases_per_operation=0,
            fuzzing_negative_cases_per_operation=0,
            seed=5,
        ),
    )

    assert result.record.received_case_count == 1
    assert result.batch is not None
    assert "phase:examples" in result.batch.cases[0].tags
    assert result.batch.cases[0].steps[0].request.query[0].value == 7


def test_rejects_configuration_without_any_generation_source() -> None:
    values = {
        "include_examples": False,
        "include_coverage": False,
        "fuzzing_positive_cases_per_operation": 0,
        "fuzzing_negative_cases_per_operation": 0,
    }
    with pytest.raises(ValidationError, match="generation source must be enabled"):
        SchemathesisGenerationConfig(**values)


@pytest.mark.parametrize("count", [-1, 101])
def test_rejects_invalid_per_operation_fuzzing_count(count: int) -> None:
    with pytest.raises(ValidationError):
        SchemathesisGenerationConfig(
            fuzzing_positive_cases_per_operation=count,
        )
