import pytest
from pydantic import ValidationError

from openapi_ai_test_evaluator.domain import GenerationConfig


def test_generation_config_has_reproducible_defaults() -> None:
    config = GenerationConfig(
        model="deepseek-v4-flash",
        prompt_version="api-cases-v1",
    )

    assert config.max_cases == 20
    assert config.max_steps_per_case == 5
    assert config.temperature == 0.0
    assert config.max_output_tokens == 4096
    assert config.timeout_ms == 60_000
    assert config.seed is None


def test_generation_config_accepts_common_provider_independent_settings() -> None:
    config = GenerationConfig(
        model="deepseek-v4-flash",
        prompt_version="api-cases-v2",
        max_cases=12,
        max_steps_per_case=4,
        temperature=0.2,
        max_output_tokens=8000,
        timeout_ms=30_000,
        seed=7,
    )

    assert config.model == "deepseek-v4-flash"
    assert config.max_cases == 12
    assert config.seed == 7


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_cases", 0),
        ("max_cases", 101),
        ("max_steps_per_case", 0),
        ("max_steps_per_case", 21),
        ("temperature", -0.1),
        ("temperature", 2.1),
        ("temperature", float("inf")),
        ("max_output_tokens", 0),
        ("timeout_ms", 0),
        ("timeout_ms", 300_001),
    ],
)
def test_generation_config_rejects_invalid_bounds(field: str, value: object) -> None:
    raw = {
        "model": "deepseek-v4-flash",
        "prompt_version": "api-cases-v1",
        field: value,
    }

    with pytest.raises(ValidationError):
        GenerationConfig.model_validate(raw)


@pytest.mark.parametrize("field", ["model", "prompt_version"])
def test_generation_config_rejects_empty_required_strings(field: str) -> None:
    raw = {
        "model": "deepseek-v4-flash",
        "prompt_version": "api-cases-v1",
        field: "   ",
    }

    with pytest.raises(ValidationError, match="at least 1 character"):
        GenerationConfig.model_validate(raw)


def test_generation_config_rejects_credentials_and_vendor_fields() -> None:
    raw = {
        "model": "deepseek-v4-flash",
        "prompt_version": "api-cases-v1",
        "api_key": "must-not-be-recorded",
        "deepseek_beta_flag": True,
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GenerationConfig.model_validate(raw)
