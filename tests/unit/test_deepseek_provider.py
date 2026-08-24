import json
from collections.abc import Callable

import httpx
import pytest

from openapi_ai_test_evaluator.generation import (
    DEEPSEEK_API_KEY_ENV,
    DeepSeekProvider,
    DeepSeekProviderConfigError,
    LLMProvider,
    LLMProviderError,
    ProviderRequest,
)

API_KEY = "test-secret-key"


def provider_request(**overrides: object) -> ProviderRequest:
    values = {
        "model": "deepseek-v4-flash",
        "system_prompt": "Return only JSON test cases.",
        "user_prompt": '{"task":"Generate API tests"}',
        "response_schema": {
            "type": "object",
            "properties": {"schema_version": {"const": "1.0"}},
        },
        "temperature": 0.2,
        "max_output_tokens": 8000,
        "timeout_ms": 30_000,
        **overrides,
    }
    return ProviderRequest.model_validate(values)


def success_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "completion-001",
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": '{"schema_version":"1.0","cases":[]}',
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_cache_hit_tokens": 40,
            "prompt_cache_miss_tokens": 60,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }
    payload.update(overrides)
    return payload


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_deepseek_provider_satisfies_common_protocol() -> None:
    with client_for(lambda request: httpx.Response(200, json=success_payload())) as client:
        provider = DeepSeekProvider(API_KEY, client=client)

        assert isinstance(provider, LLMProvider)
        assert provider.name == "deepseek"


def test_maps_provider_request_to_deepseek_json_mode() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=success_payload())

    common_request = provider_request()
    with client_for(handler) as client:
        DeepSeekProvider(API_KEY, client=client).generate(common_request)

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == f"Bearer {API_KEY}"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "deepseek-v4-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 8000
    assert body["stream"] is False
    assert "seed" not in body

    messages = body["messages"]
    assert isinstance(messages, list)
    assert messages[0] == {"role": "system", "content": common_request.system_prompt}
    user_content = messages[1]["content"]
    assert isinstance(user_content, str)
    prompt, schema_text = user_content.rsplit("\n\nRESPONSE_JSON_SCHEMA:\n", 1)
    assert prompt == common_request.user_prompt
    assert json.loads(schema_text) == common_request.response_schema
    assert API_KEY not in json.dumps(body)


def test_maps_deepseek_response_and_usage() -> None:
    with client_for(lambda request: httpx.Response(200, json=success_payload())) as client:
        response = DeepSeekProvider(API_KEY, client=client).generate(provider_request())

    assert response.output_text == '{"schema_version":"1.0","cases":[]}'
    assert response.model == "deepseek-v4-flash"
    assert response.request_id == "completion-001"
    assert response.finish_reason == "stop"
    assert response.token_usage.input_tokens == 100
    assert response.token_usage.output_tokens == 20
    assert response.token_usage.total_tokens == 120
    assert response.token_usage.cached_input_tokens == 40
    assert response.token_usage.reasoning_tokens == 0


def test_creates_provider_from_environment_mapping() -> None:
    with client_for(lambda request: httpx.Response(200, json=success_payload())) as client:
        provider = DeepSeekProvider.from_env(
            env={DEEPSEEK_API_KEY_ENV: API_KEY},
            client=client,
        )
        response = provider.generate(provider_request())

    assert response.request_id == "completion-001"


def test_rejects_missing_environment_key() -> None:
    with pytest.raises(DeepSeekProviderConfigError, match=DEEPSEEK_API_KEY_ENV):
        DeepSeekProvider.from_env(env={})


@pytest.mark.parametrize("api_key", ["", "   "])
def test_rejects_empty_api_key(api_key: str) -> None:
    with pytest.raises(DeepSeekProviderConfigError, match="cannot be empty"):
        DeepSeekProvider(api_key)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.deepseek.com",
        "https://user:password@api.deepseek.com",
        "https://api.deepseek.com?key=secret",
    ],
)
def test_rejects_unsafe_base_url(base_url: str) -> None:
    with pytest.raises(DeepSeekProviderConfigError, match="HTTPS origin"):
        DeepSeekProvider(API_KEY, base_url=base_url)


def test_rejects_unsupported_seed_before_sending_request() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=success_payload())

    with client_for(handler) as client:
        provider = DeepSeekProvider(API_KEY, client=client)
        with pytest.raises(LLMProviderError) as raised:
            provider.generate(provider_request(seed=7))

    assert raised.value.code == "unsupported-parameter"
    assert raised.value.retryable is False
    assert called is False


@pytest.mark.parametrize(
    ("status", "expected_code", "retryable"),
    [
        (400, "invalid-request", False),
        (401, "authentication-failed", False),
        (402, "insufficient-balance", False),
        (422, "invalid-parameters", False),
        (429, "rate-limited", True),
        (500, "server-error", True),
        (503, "server-overloaded", True),
        (502, "http-502", True),
    ],
)
def test_maps_http_errors_without_exposing_response_body(
    status: int,
    expected_code: str,
    retryable: bool,
) -> None:
    secret_body = "sensitive-provider-error"
    with client_for(lambda request: httpx.Response(status, text=secret_body)) as client:
        provider = DeepSeekProvider(API_KEY, client=client)
        with pytest.raises(LLMProviderError) as raised:
            provider.generate(provider_request())

    assert raised.value.code == expected_code
    assert raised.value.retryable is retryable
    assert secret_body not in raised.value.message
    assert API_KEY not in raised.value.message


def test_maps_timeout_to_retryable_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with client_for(handler) as client:
        provider = DeepSeekProvider(API_KEY, client=client)
        with pytest.raises(LLMProviderError) as raised:
            provider.generate(provider_request())

    assert raised.value.code == "timeout"
    assert raised.value.retryable is True


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (
            {"id": "completion-001", "model": "deepseek-v4-flash", "choices": []},
            "invalid-provider-response",
        ),
        (
            success_payload(choices=[{"finish_reason": "stop", "message": {"content": ""}}]),
            "empty-output",
        ),
        (success_payload(usage={"prompt_tokens": -1}), "invalid-provider-response"),
    ],
)
def test_rejects_malformed_or_empty_success_response(
    payload: dict[str, object],
    expected_code: str,
) -> None:
    with client_for(lambda request: httpx.Response(200, json=payload)) as client:
        provider = DeepSeekProvider(API_KEY, client=client)
        with pytest.raises(LLMProviderError) as raised:
            provider.generate(provider_request())

    assert raised.value.code == expected_code
    assert raised.value.retryable is True


def test_rejects_non_json_success_response() -> None:
    with client_for(lambda request: httpx.Response(200, text="not-json")) as client:
        provider = DeepSeekProvider(API_KEY, client=client)
        with pytest.raises(LLMProviderError) as raised:
            provider.generate(provider_request())

    assert raised.value.code == "invalid-provider-response"
