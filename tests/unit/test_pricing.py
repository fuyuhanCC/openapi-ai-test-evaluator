from datetime import UTC, datetime

import pytest

from openapi_ai_test_evaluator.domain.generation import GenerationTokenUsage
from openapi_ai_test_evaluator.domain.pricing import TokenPricingSnapshot
from openapi_ai_test_evaluator.evaluation.pricing import (
    PricingCalculationError,
    estimate_token_cost_usd,
)


def pricing(**overrides: object) -> TokenPricingSnapshot:
    values = {
        "pricing_id": "deepseek-v4-flash-peak",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "rate_class": "peak",
        "cached_input_usd_per_unit": 0.014,
        "uncached_input_usd_per_unit": 0.44,
        "output_usd_per_unit": 1.32,
        "effective_from": datetime(2026, 8, 16, 16, tzinfo=UTC),
        "captured_at": datetime(2026, 9, 1, 2, 34, 7, tzinfo=UTC),
        "source_url": "https://api-docs.deepseek.com/quick_start/pricing/",
        **overrides,
    }
    return TokenPricingSnapshot.model_validate(values)


def test_prices_cached_uncached_and_output_tokens_separately() -> None:
    usage = GenerationTokenUsage(
        input_tokens=4760,
        cached_input_tokens=4736,
        output_tokens=4659,
        total_tokens=9419,
    )

    assert estimate_token_cost_usd(usage, pricing()) == pytest.approx(0.006226744)


def test_returns_none_when_distinct_cache_rates_cannot_be_applied() -> None:
    usage = GenerationTokenUsage(input_tokens=100, output_tokens=50)

    assert estimate_token_cost_usd(usage, pricing()) is None


def test_rejects_cached_tokens_above_total_input() -> None:
    usage = GenerationTokenUsage(
        input_tokens=10,
        cached_input_tokens=11,
        output_tokens=1,
    )

    with pytest.raises(PricingCalculationError, match="cannot exceed"):
        estimate_token_cost_usd(usage, pricing())


def test_pricing_snapshot_requires_timezone_aware_timestamps() -> None:
    with pytest.raises(ValueError, match="must include a timezone"):
        pricing(captured_at=datetime(2026, 9, 1))
