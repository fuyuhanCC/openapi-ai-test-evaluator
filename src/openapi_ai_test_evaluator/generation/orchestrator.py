"""Orchestrate one provider call into test cases and generation metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter_ns

from openapi_ai_test_evaluator.domain.generation import (
    GenerationError,
    GenerationRecord,
    GenerationStatus,
    GenerationTokenUsage,
)
from openapi_ai_test_evaluator.domain.test_case import TestCaseBatch
from openapi_ai_test_evaluator.generation.provider import (
    LLMProvider,
    LLMProviderError,
    ProviderRequest,
)
from openapi_ai_test_evaluator.validation import (
    TestCaseBatchLoadError,
    parse_test_case_batch,
)


@dataclass(frozen=True, slots=True)
class GenerationAttempt:
    """In-memory pairing of separate generation metadata and optional cases."""

    record: GenerationRecord
    batch: TestCaseBatch | None

    def __post_init__(self) -> None:
        succeeded = self.record.status is GenerationStatus.SUCCEEDED
        if succeeded != (self.batch is not None):
            raise ValueError("only successful generation attempts may contain a batch")


def generate_test_case_batch(
    provider: LLMProvider,
    request: ProviderRequest,
    *,
    generation_id: str,
    prompt_version: str,
) -> GenerationAttempt:
    """Call one provider and convert its JSON output into a TestCaseBatch attempt."""
    started_at = datetime.now(UTC)
    started_timer = perf_counter_ns()
    try:
        response = provider.generate(request)
    except LLMProviderError as error:
        return GenerationAttempt(
            record=_record(
                generation_id=generation_id,
                provider=provider.name,
                model=request.model,
                prompt_version=prompt_version,
                started_at=started_at,
                started_timer=started_timer,
                status=GenerationStatus.PROVIDER_ERROR,
                error=GenerationError(
                    code=error.code,
                    message=error.message,
                    retryable=error.retryable,
                ),
            ),
            batch=None,
        )

    try:
        batch = parse_test_case_batch(
            response.output_text,
            source=f"{provider.name} provider output",
        )
    except TestCaseBatchLoadError:
        return GenerationAttempt(
            record=_record(
                generation_id=generation_id,
                provider=provider.name,
                model=response.model,
                prompt_version=prompt_version,
                started_at=started_at,
                started_timer=started_timer,
                status=GenerationStatus.INVALID_OUTPUT,
                provider_request_id=response.request_id,
                finish_reason=response.finish_reason,
                token_usage=response.token_usage,
                error=GenerationError(
                    code="invalid-test-case-batch",
                    message="provider output was not valid TestCaseBatch JSON",
                    retryable=True,
                ),
            ),
            batch=None,
        )

    return GenerationAttempt(
        record=_record(
            generation_id=generation_id,
            provider=provider.name,
            model=response.model,
            prompt_version=prompt_version,
            started_at=started_at,
            started_timer=started_timer,
            status=GenerationStatus.SUCCEEDED,
            provider_request_id=response.request_id,
            finish_reason=response.finish_reason,
            token_usage=response.token_usage,
        ),
        batch=batch,
    )


def _record(
    *,
    generation_id: str,
    provider: str,
    model: str,
    prompt_version: str,
    started_at: datetime,
    started_timer: int,
    status: GenerationStatus,
    provider_request_id: str | None = None,
    finish_reason: str | None = None,
    token_usage: GenerationTokenUsage | None = None,
    error: GenerationError | None = None,
) -> GenerationRecord:
    finished_at = datetime.now(UTC)
    return GenerationRecord(
        schema_version="1.0",
        kind="GenerationRecord",
        generation_id=generation_id,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        provider_request_id=provider_request_id,
        finish_reason=finish_reason,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=max(0, (perf_counter_ns() - started_timer) // 1_000_000),
        status=status,
        request_count=1,
        token_usage=token_usage or GenerationTokenUsage(),
        estimated_cost_usd=None,
        error=error,
    )
