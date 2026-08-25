"""End-to-end generation pipeline shared by all LLM providers."""

from __future__ import annotations

from openapi_ai_test_evaluator.domain.generation import (
    GenerationConfig,
    GenerationError,
    GenerationRecord,
    GenerationStatus,
)
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.domain.test_case import TestCaseBatch
from openapi_ai_test_evaluator.generation.orchestrator import (
    GenerationAttempt,
    generate_test_case_batch,
)
from openapi_ai_test_evaluator.generation.prompt_builder import build_provider_request
from openapi_ai_test_evaluator.generation.provider import LLMProvider
from openapi_ai_test_evaluator.validation import validate_test_case_batch_semantics


def generate_cases_from_openapi(
    provider: LLMProvider,
    spec: OpenAPISpec,
    config: GenerationConfig,
    *,
    generation_id: str,
) -> GenerationAttempt:
    """Build a prompt, call one provider, and validate its generated cases."""
    request = build_provider_request(spec, config)
    attempt = generate_test_case_batch(
        provider,
        request,
        generation_id=generation_id,
        prompt_version=config.prompt_version,
    )
    if attempt.batch is None:
        return attempt

    limit_error = _generation_limit_error(attempt.batch, config)
    if limit_error is not None:
        return _invalid_attempt(attempt, limit_error)

    semantic_issues = validate_test_case_batch_semantics(attempt.batch, spec)
    if semantic_issues:
        issue_codes = ", ".join(sorted({issue.code for issue in semantic_issues}))
        return _invalid_attempt(
            attempt,
            GenerationError(
                code="semantic-validation-failed",
                message=(
                    "generated test cases failed OpenAPI semantic validation "
                    f"({len(semantic_issues)} issues: {issue_codes})"
                ),
                retryable=True,
            ),
        )

    return attempt


def _generation_limit_error(
    batch: TestCaseBatch,
    config: GenerationConfig,
) -> GenerationError | None:
    if len(batch.cases) > config.max_cases:
        return GenerationError(
            code="generation-limits-exceeded",
            message=(f"provider returned {len(batch.cases)} cases; maximum is {config.max_cases}"),
            retryable=True,
        )

    for case in batch.cases:
        step_count = len(case.setup) + len(case.steps) + len(case.cleanup)
        if step_count > config.max_steps_per_case:
            return GenerationError(
                code="generation-limits-exceeded",
                message=(
                    f"case {case.id!r} contains {step_count} total steps; maximum is "
                    f"{config.max_steps_per_case}"
                ),
                retryable=True,
            )
    return None


def _invalid_attempt(attempt: GenerationAttempt, error: GenerationError) -> GenerationAttempt:
    raw_record = attempt.record.model_dump()
    raw_record.update(
        {
            "status": GenerationStatus.INVALID_OUTPUT,
            "error": error,
        }
    )
    return GenerationAttempt(
        record=GenerationRecord.model_validate(raw_record),
        batch=None,
        provider_output_text=attempt.provider_output_text,
    )
