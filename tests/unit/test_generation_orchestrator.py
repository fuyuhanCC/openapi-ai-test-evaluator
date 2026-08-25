import json

import pytest

from openapi_ai_test_evaluator.domain.generation import GenerationStatus
from openapi_ai_test_evaluator.generation import (
    FakeProvider,
    GenerationAttempt,
    LLMProviderError,
    ProviderRequest,
    ProviderResponse,
    generate_test_case_batch,
)


def provider_request() -> ProviderRequest:
    return ProviderRequest(
        model="deepseek-v4-flash",
        system_prompt="Return only TestCaseBatch JSON.",
        user_prompt="Generate a listItems API test.",
        response_schema={"type": "object"},
    )


def valid_output() -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "cases": [
                {
                    "id": "list-items",
                    "steps": [{"id": "list", "operation_id": "listItems"}],
                }
            ],
        }
    )


def provider_response(output_text: str = "") -> ProviderResponse:
    return ProviderResponse(
        output_text=output_text or valid_output(),
        model="deepseek-v4-flash",
        request_id="provider-request-1",
        finish_reason="stop",
        token_usage={
            "input_tokens": 100,
            "output_tokens": 25,
            "total_tokens": 125,
        },
    )


def generate(provider: FakeProvider) -> GenerationAttempt:
    return generate_test_case_batch(
        provider,
        provider_request(),
        generation_id="generation-001",
        prompt_version="api-cases-v1",
    )


def test_generates_validated_batch_and_success_record() -> None:
    provider = FakeProvider(response=provider_response(), name="deepseek")

    attempt = generate(provider)

    assert attempt.batch is not None
    assert attempt.batch.cases[0].id == "list-items"
    assert attempt.record.status is GenerationStatus.SUCCEEDED
    assert attempt.record.provider == "deepseek"
    assert attempt.record.model == "deepseek-v4-flash"
    assert attempt.record.provider_request_id == "provider-request-1"
    assert attempt.record.token_usage.total_tokens == 125
    assert attempt.record.request_count == 1
    assert attempt.record.error is None
    assert attempt.provider_output_text == valid_output()
    assert provider.requests == (provider_request(),)


@pytest.mark.parametrize(
    "output_text",
    [
        "not-json",
        json.dumps({"schema_version": "1.0", "cases": []}),
        f"```json\n{valid_output()}\n```",
    ],
)
def test_records_invalid_provider_output_without_returning_cases(output_text: str) -> None:
    provider = FakeProvider(response=provider_response(output_text), name="deepseek")

    attempt = generate(provider)

    assert attempt.batch is None
    assert attempt.record.status is GenerationStatus.INVALID_OUTPUT
    assert attempt.record.model == "deepseek-v4-flash"
    assert attempt.record.token_usage.total_tokens == 125
    assert attempt.record.error is not None
    assert attempt.record.error.code == "invalid-test-case-batch"
    assert attempt.record.error.retryable is True
    assert output_text not in attempt.record.error.message
    assert attempt.provider_output_text == output_text


def test_records_provider_error_without_token_usage() -> None:
    provider = FakeProvider(
        error=LLMProviderError("rate-limited", "provider quota exceeded", retryable=True),
        name="deepseek",
    )

    attempt = generate(provider)

    assert attempt.batch is None
    assert attempt.record.status is GenerationStatus.PROVIDER_ERROR
    assert attempt.record.model == "deepseek-v4-flash"
    assert attempt.record.provider_request_id is None
    assert attempt.record.token_usage.total_tokens is None
    assert attempt.record.error is not None
    assert attempt.record.error.code == "rate-limited"
    assert attempt.record.error.retryable is True
    assert attempt.provider_output_text is None


def test_attempt_rejects_batch_status_mismatch() -> None:
    successful = generate(FakeProvider(response=provider_response()))
    assert successful.batch is not None

    with pytest.raises(ValueError, match="only successful"):
        GenerationAttempt(record=successful.record, batch=None)
