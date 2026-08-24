import pytest
from pydantic import ValidationError

from openapi_ai_test_evaluator.generation import (
    FakeProvider,
    LLMProvider,
    LLMProviderError,
    ProviderRequest,
    ProviderResponse,
)


def request() -> ProviderRequest:
    return ProviderRequest(
        model="deepseek-v4-flash",
        system_prompt="Generate API test cases as JSON.",
        user_prompt="Create tests for the listItems operation.",
        response_schema={"type": "object", "properties": {}},
        temperature=0.2,
        max_output_tokens=2000,
        timeout_ms=30_000,
        seed=7,
    )


def response() -> ProviderResponse:
    return ProviderResponse(
        output_text='{"schema_version":"1.0","cases":[]}',
        model="deepseek-v4-flash",
        request_id="provider-request-1",
        finish_reason="stop",
        token_usage={
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
        },
    )


def test_fake_provider_satisfies_provider_protocol() -> None:
    provider = FakeProvider(response=response(), name="deepseek-fixture")

    assert isinstance(provider, LLMProvider)
    assert provider.name == "deepseek-fixture"


def test_fake_provider_returns_response_and_records_request() -> None:
    provider = FakeProvider(response=response())
    provider_request = request()

    actual = provider.generate(provider_request)

    assert actual.model == "deepseek-v4-flash"
    assert actual.token_usage.total_tokens == 120
    assert provider.requests == (provider_request,)


def test_fake_provider_returns_defensive_copies() -> None:
    configured_response = response()
    provider = FakeProvider(response=configured_response)

    actual = provider.generate(request())
    actual.output_text = "changed"

    assert provider.generate(request()).output_text == configured_response.output_text


def test_fake_provider_propagates_stable_error_metadata() -> None:
    error = LLMProviderError("rate-limited", "request quota exceeded", retryable=True)
    provider = FakeProvider(error=error)

    with pytest.raises(LLMProviderError) as raised:
        provider.generate(request())

    assert raised.value.code == "rate-limited"
    assert raised.value.retryable is True
    assert len(provider.requests) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"response": response(), "error": LLMProviderError("failed", "failed", retryable=False)},
    ],
)
def test_fake_provider_requires_exactly_one_outcome(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        FakeProvider(**kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", float("inf")),
        ("temperature", -0.1),
        ("max_output_tokens", 0),
        ("timeout_ms", 0),
    ],
)
def test_provider_request_rejects_invalid_common_parameters(field: str, value: object) -> None:
    raw = request().model_dump()
    raw[field] = value

    with pytest.raises(ValidationError):
        ProviderRequest.model_validate(raw)


def test_provider_request_rejects_credentials_and_vendor_fields() -> None:
    raw = request().model_dump()
    raw["api_key"] = "must-not-enter-recordable-request"
    raw["deepseek_beta_flag"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProviderRequest.model_validate(raw)


def test_provider_response_rejects_raw_vendor_payload() -> None:
    raw = response().model_dump()
    raw["raw_response"] = {"secret": "unsafe"}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProviderResponse.model_validate(raw)
