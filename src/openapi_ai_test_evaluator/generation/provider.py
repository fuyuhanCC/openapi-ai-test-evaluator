"""Stable contracts shared by all LLM provider adapters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import Field, JsonValue

from openapi_ai_test_evaluator.domain.contracts import ContractModel
from openapi_ai_test_evaluator.domain.generation import GenerationTokenUsage


class ProviderRequest(ContractModel):
    """One structured-output request without credentials or vendor-only fields."""

    model: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    response_schema: dict[str, JsonValue] = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0, allow_inf_nan=False)
    max_output_tokens: int = Field(default=4096, ge=1)
    timeout_ms: int = Field(default=60_000, ge=1, le=300_000)
    seed: int | None = None


class ProviderResponse(ContractModel):
    """Sanitized provider response consumed by generation orchestration."""

    output_text: str = Field(min_length=1)
    model: str = Field(min_length=1)
    request_id: str | None = Field(default=None, min_length=1)
    finish_reason: str | None = Field(default=None, min_length=1)
    token_usage: GenerationTokenUsage = Field(default_factory=GenerationTokenUsage)


class LLMProviderError(RuntimeError):
    """A sanitized provider failure with stable retry metadata."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        if not code.strip():
            raise ValueError("provider error code cannot be empty")
        if not message.strip():
            raise ValueError("provider error message cannot be empty")
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"{code}: {message}")


@runtime_checkable
class LLMProvider(Protocol):
    """Synchronous provider boundary implemented by each model vendor adapter."""

    @property
    def name(self) -> str:
        """Return the stable provider identifier stored in artifacts."""
        ...

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Submit one structured-output generation request."""
        ...
