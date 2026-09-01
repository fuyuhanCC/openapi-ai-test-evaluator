"""Versioned token-pricing snapshots used by reproducible benchmarks."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, field_validator

from openapi_ai_test_evaluator.domain.contracts import ContractModel, Identifier

PositiveInt = Annotated[int, Field(ge=1)]
NonNegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class TokenPricingSnapshot(ContractModel):
    """One explicit provider/model/rate-class price captured for an experiment."""

    pricing_id: Identifier
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    rate_class: str = Field(min_length=1)
    currency: Literal["USD"] = "USD"
    unit_tokens: PositiveInt = 1_000_000
    cached_input_usd_per_unit: NonNegativeFloat
    uncached_input_usd_per_unit: NonNegativeFloat
    output_usd_per_unit: NonNegativeFloat
    effective_from: datetime
    captured_at: datetime
    source_url: AnyHttpUrl

    @field_validator("effective_from", "captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("pricing timestamps must include a timezone")
        return value


__all__ = ["TokenPricingSnapshot"]
