"""End-to-end generation pipeline shared by all LLM providers."""

from __future__ import annotations

from openapi_ai_test_evaluator.domain.generation import (
    GenerationConfig,
    GenerationError,
    GenerationRecord,
    GenerationStatus,
)
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.generation.case_admission import (
    GeneratedCaseAdmission,
    ProviderOutputAdmissionError,
    admit_generated_cases,
)
from openapi_ai_test_evaluator.generation.orchestrator import (
    GenerationAttempt,
    generate_test_case_batch,
)
from openapi_ai_test_evaluator.generation.prompt_builder import build_provider_request
from openapi_ai_test_evaluator.generation.provider import LLMProvider


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
    if attempt.provider_output_text is None:
        return attempt

    try:
        admission = admit_generated_cases(attempt.provider_output_text, spec, config)
    except ProviderOutputAdmissionError:
        return attempt
    return _attempt_with_admission(attempt, admission)


def _attempt_with_admission(
    attempt: GenerationAttempt,
    admission: GeneratedCaseAdmission,
) -> GenerationAttempt:
    raw_record = attempt.record.model_dump()
    raw_record["case_admission"] = admission.summary
    if admission.batch is None:
        raw_record.update(
            {
                "status": GenerationStatus.INVALID_OUTPUT,
                "error": GenerationError(
                    code="no-admitted-test-cases",
                    message="provider output contained no executable test cases",
                    retryable=True,
                ),
            }
        )
    else:
        raw_record.update({"status": GenerationStatus.SUCCEEDED, "error": None})
    return GenerationAttempt(
        record=GenerationRecord.model_validate(raw_record),
        batch=admission.batch,
        provider_output_text=attempt.provider_output_text,
    )
