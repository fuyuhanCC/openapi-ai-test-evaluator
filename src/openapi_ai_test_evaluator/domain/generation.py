"""Provider-independent records for one test-case generation attempt."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from openapi_ai_test_evaluator.domain.contracts import ContractModel, Identifier

NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]
NonNegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class GenerationConfig(ContractModel):
    """Reproducible, provider-independent settings for one generation request."""

    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    max_cases: int = Field(default=20, ge=1, le=100)
    max_steps_per_case: int = Field(default=5, ge=1, le=20)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0, allow_inf_nan=False)
    max_output_tokens: int = Field(default=4096, ge=1)
    timeout_ms: int = Field(default=60_000, ge=1, le=300_000)
    seed: int | None = None


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


class CaseAdmissionStage(StrEnum):
    STRUCTURE = "structure"
    LIMIT = "limit"
    SEMANTIC = "semantic"


class CaseAdmissionRejection(ContractModel):
    """Sanitized reason one LLM-produced case was not admitted for execution."""

    index: NonNegativeInt
    case_id: str | None = Field(default=None, min_length=1)
    stage: CaseAdmissionStage
    code: str = Field(min_length=1)
    detail_codes: list[str] = Field(default_factory=list)

    @field_validator("detail_codes")
    @classmethod
    def require_unique_detail_codes(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("admission detail codes cannot be empty")
        if len(values) != len(set(values)):
            raise ValueError("admission detail codes must be unique")
        return values


class CaseAdmissionSummary(ContractModel):
    """Per-case admission counts for a structurally decodable provider batch."""

    received_case_count: NonNegativeInt
    admitted_case_count: NonNegativeInt
    rejected_case_count: NonNegativeInt
    rejections: list[CaseAdmissionRejection] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.received_case_count != self.admitted_case_count + self.rejected_case_count:
            raise ValueError("received cases must equal admitted plus rejected cases")
        if len(self.rejections) != self.rejected_case_count:
            raise ValueError("one admission rejection is required per rejected case")
        rejection_indexes = [rejection.index for rejection in self.rejections]
        if len(rejection_indexes) != len(set(rejection_indexes)):
            raise ValueError("admission rejection indexes must be unique")
        if any(index >= self.received_case_count for index in rejection_indexes):
            raise ValueError("admission rejection index is outside the received case list")
        return self


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
    case_admission: CaseAdmissionSummary | None = None
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
        if self.case_admission is not None:
            admitted = self.case_admission.admitted_case_count
            if self.status is GenerationStatus.SUCCEEDED and admitted == 0:
                raise ValueError("successful generation records require an admitted case")
            if self.status is not GenerationStatus.SUCCEEDED and admitted > 0:
                raise ValueError("unsuccessful generation records cannot contain admitted cases")
        return self


class AdaptationSkipReason(ContractModel):
    """Stable primary reason why one or more baseline cases were rejected."""

    code: str = Field(min_length=1)
    detail_code: str | None = Field(default=None, min_length=1)
    count: PositiveInt


class AdaptationRecord(ContractModel):
    """Reproducible summary of one conventional-tool adaptation attempt."""

    schema_version: Literal["1.0"]
    kind: Literal["AdaptationRecord"]
    tool: Literal["schemathesis"]
    tool_version: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    seed: int | None = None
    received_case_count: NonNegativeInt
    adapted_case_count: NonNegativeInt
    rejected_case_count: NonNegativeInt
    skip_reasons: list[AdaptationSkipReason] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.received_case_count != self.adapted_case_count + self.rejected_case_count:
            raise ValueError("received cases must equal adapted plus rejected cases")

        reason_keys = [(reason.code, reason.detail_code) for reason in self.skip_reasons]
        if len(reason_keys) != len(set(reason_keys)):
            raise ValueError("adaptation skip reasons must be unique")
        if sum(reason.count for reason in self.skip_reasons) != self.rejected_case_count:
            raise ValueError("skip reason counts must equal rejected case count")
        return self
