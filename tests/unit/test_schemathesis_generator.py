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


def test_generates_requested_cases_from_all_available_phases_without_http() -> None:
    schema = schemathesis.openapi.from_path(SPEC_PATH)

    result = generate_schemathesis_batch(
        schema,
        SPEC,
        SchemathesisGenerationConfig(
            example_case_limit=0,
            coverage_positive_case_limit=2,
            coverage_negative_case_limit=2,
            fuzzing_positive_case_count=3,
            fuzzing_negative_case_count=2,
            seed=7,
        ),
    )

    assert result.record.received_case_count == 9
    assert result.record.adapted_case_count + result.record.rejected_case_count == 9
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
        example_case_limit=0,
        coverage_positive_case_limit=2,
        coverage_negative_case_limit=2,
        fuzzing_positive_case_count=3,
        fuzzing_negative_case_count=2,
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


def test_can_allocate_the_whole_budget_to_one_generation_mode() -> None:
    result = generate_schemathesis_batch(
        schemathesis.openapi.from_path(SPEC_PATH),
        SPEC,
        SchemathesisGenerationConfig(
            example_case_limit=0,
            coverage_positive_case_limit=0,
            coverage_negative_case_limit=0,
            fuzzing_positive_case_count=2,
            fuzzing_negative_case_count=0,
            seed=3,
        ),
    )

    assert result.record.received_case_count == 2
    if result.batch is not None:
        assert all("mode:positive" in case.tags for case in result.batch.cases)


def test_collects_explicit_openapi_examples() -> None:
    raw_schema = schemathesis.openapi.from_path(SPEC_PATH).raw_schema
    raw_schema["paths"]["/items"]["get"]["parameters"][3]["example"] = 7
    schema = schemathesis.openapi.from_dict(raw_schema)

    result = generate_schemathesis_batch(
        schema,
        SPEC,
        SchemathesisGenerationConfig(
            example_case_limit=1,
            coverage_positive_case_limit=0,
            coverage_negative_case_limit=0,
            fuzzing_positive_case_count=0,
            fuzzing_negative_case_count=0,
            seed=5,
        ),
    )

    assert result.record.received_case_count == 1
    assert result.batch is not None
    assert "phase:examples" in result.batch.cases[0].tags
    assert result.batch.cases[0].steps[0].request.query[0].value == 7


@pytest.mark.parametrize("changes", [{}, {"example_case_limit": -1}])
def test_rejects_invalid_generation_budgets(changes: dict[str, int]) -> None:
    values = {
        "example_case_limit": 0,
        "coverage_positive_case_limit": 0,
        "coverage_negative_case_limit": 0,
        "fuzzing_positive_case_count": 0,
        "fuzzing_negative_case_count": 0,
        **changes,
    }
    with pytest.raises(ValidationError):
        SchemathesisGenerationConfig(**values)


def test_rejects_combined_phase_budget_over_one_hundred() -> None:
    with pytest.raises(ValidationError, match="cannot exceed 100"):
        SchemathesisGenerationConfig(
            example_case_limit=21,
            coverage_positive_case_limit=20,
            coverage_negative_case_limit=20,
            fuzzing_positive_case_count=20,
            fuzzing_negative_case_count=20,
        )
