"""Pure token-cost calculation from frozen usage and pricing snapshots."""

from __future__ import annotations

from decimal import Decimal

from openapi_ai_test_evaluator.domain.generation import GenerationTokenUsage
from openapi_ai_test_evaluator.domain.pricing import TokenPricingSnapshot


class PricingCalculationError(ValueError):
    """Reported token usage cannot be priced by the selected snapshot."""


def estimate_token_cost_usd(
    usage: GenerationTokenUsage,
    pricing: TokenPricingSnapshot,
) -> float | None:
    """Price one generation call, returning None when required usage is absent."""
    if usage.input_tokens is None or usage.output_tokens is None:
        return None

    cached_tokens = usage.cached_input_tokens
    if cached_tokens is None:
        if pricing.cached_input_usd_per_unit != pricing.uncached_input_usd_per_unit:
            return None
        cached_tokens = 0
    if cached_tokens > usage.input_tokens:
        raise PricingCalculationError("cached input tokens cannot exceed total input tokens")

    uncached_tokens = usage.input_tokens - cached_tokens
    cost = (
        Decimal(uncached_tokens) * Decimal(str(pricing.uncached_input_usd_per_unit))
        + Decimal(cached_tokens) * Decimal(str(pricing.cached_input_usd_per_unit))
        + Decimal(usage.output_tokens) * Decimal(str(pricing.output_usd_per_unit))
    ) / Decimal(pricing.unit_tokens)
    return float(cost)


__all__ = ["PricingCalculationError", "estimate_token_cost_usd"]
