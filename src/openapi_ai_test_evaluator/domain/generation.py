"""Provider-independent records for one test-case generation attempt."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from openapi_ai_test_evaluator.domain.contracts import ContractModel, Identifier

NonNegativeInt = Annotated[int, Field(ge=0)]
NonNegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class GenerationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    INVALID_OUTPUT = "invalid_output"
    PROVIDER_ERROR = "provider_error"
    BUDGET_EXCEEDED = "budget_exceeded"


class GenerationTokenUsage(ContractModel):
    """Token counts reported by a provider when available."""

    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    total_tokens: NonNegativeInt | None = None
    cached_input_tokens: NonNegativeInt | None = None
    reasoning_tokens: NonNegativeInt | None = None


class GenerationError(ContractModel):
    """Sanitized failure details safe to store in experiment artifacts."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool


class GenerationRecord(ContractModel):
    """Metadata and resource usage for one provider generation attempt."""

    schema_version: Literal["1.0"]
    kind: Literal["GenerationRecord"]
    generation_id: Identifier
    provider: str = Field(min_length=1)
    model: str | None = Field(default=None, min_length=1)
    prompt_version: str | None = Field(default=None, min_length=1)
    provider_request_id: str | None = Field(default=None, min_length=1)
    finish_reason: str | None = Field(default=None, min_length=1)
    started_at: datetime
    finished_at: datetime
    duration_ms: NonNegativeInt
    status: GenerationStatus
    request_count: NonNegativeInt
    token_usage: GenerationTokenUsage = Field(default_factory=GenerationTokenUsage)
    estimated_cost_usd: NonNegativeFloat | None = None
    error: GenerationError | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generation timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_attempt(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        if self.status is GenerationStatus.SUCCEEDED and self.error is not None:
            raise ValueError("successful generation records cannot contain an error")
        if self.status is not GenerationStatus.SUCCEEDED and self.error is None:
            raise ValueError("unsuccessful generation records require an error")
        return self
