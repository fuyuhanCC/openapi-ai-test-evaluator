"""DeepSeek chat-completion adapter implemented with HTTPX."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from types import TracebackType
from urllib.parse import urlparse

import httpx

from openapi_ai_test_evaluator.domain.generation import GenerationTokenUsage
from openapi_ai_test_evaluator.generation.provider import (
    LLMProviderError,
    ProviderRequest,
    ProviderResponse,
)

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
_SCHEMA_MARKER = "\n\nRESPONSE_JSON_SCHEMA:\n"

_HTTP_ERRORS: dict[int, tuple[str, bool]] = {
    400: ("invalid-request", False),
    401: ("authentication-failed", False),
    402: ("insufficient-balance", False),
    422: ("invalid-parameters", False),
    429: ("rate-limited", True),
    500: ("server-error", True),
    503: ("server-overloaded", True),
}


class DeepSeekProviderConfigError(ValueError):
    """DeepSeek provider credentials or endpoint configuration is invalid."""


class DeepSeekProvider:
    """Translate the common provider contract to DeepSeek's HTTP API."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        normalized_key = api_key.strip()
        if not normalized_key:
            raise DeepSeekProviderConfigError("DeepSeek API key cannot be empty")
        self._base_url = _validate_base_url(base_url)
        self._api_key = normalized_key
        self._owns_client = client is None
        self._client = client or httpx.Client()

    @classmethod
    def from_env(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        client: httpx.Client | None = None,
    ) -> DeepSeekProvider:
        """Create a provider using the conventional API-key environment variable."""
        environment = os.environ if env is None else env
        api_key = environment.get(DEEPSEEK_API_KEY_ENV, "")
        if not api_key.strip():
            raise DeepSeekProviderConfigError(
                f"environment variable {DEEPSEEK_API_KEY_ENV} is not set"
            )
        return cls(api_key, base_url=base_url, client=client)

    @property
    def name(self) -> str:
        return "deepseek"

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Call DeepSeek once and return only provider-independent response fields."""
        if request.seed is not None:
            raise LLMProviderError(
                "unsupported-parameter",
                "DeepSeek Chat Completion does not support seed",
                retryable=False,
            )

        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=_request_payload(request),
                timeout=request.timeout_ms / 1000,
            )
        except httpx.TimeoutException as error:
            raise LLMProviderError(
                "timeout",
                "DeepSeek request timed out",
                retryable=True,
            ) from error
        except httpx.TransportError as error:
            raise LLMProviderError(
                "network-error",
                "DeepSeek request failed before receiving a response",
                retryable=True,
            ) from error

        if response.status_code >= 400:
            code, retryable = _HTTP_ERRORS.get(
                response.status_code,
                (f"http-{response.status_code}", response.status_code >= 500),
            )
            raise LLMProviderError(
                code,
                f"DeepSeek API returned HTTP {response.status_code}",
                retryable=retryable,
            )

        try:
            payload = response.json()
        except ValueError as error:
            raise _invalid_provider_response("DeepSeek returned a non-JSON response") from error
        return _parse_response(payload)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> DeepSeekProvider:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _validate_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise DeepSeekProviderConfigError(
            "DeepSeek base URL must be an HTTPS origin without credentials, query, or fragment"
        )
    return normalized


def _request_payload(request: ProviderRequest) -> dict[str, object]:
    schema_json = json.dumps(
        request.response_schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "model": request.model,
        "messages": [
            {"role": "system", "content": request.system_prompt},
            {
                "role": "user",
                "content": f"{request.user_prompt}{_SCHEMA_MARKER}{schema_json}",
            },
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "temperature": request.temperature,
        "max_tokens": request.max_output_tokens,
        "stream": False,
    }


def _parse_response(payload: object) -> ProviderResponse:
    if not isinstance(payload, dict):
        raise _invalid_provider_response("DeepSeek response must be a JSON object")

    request_id = _required_string(payload, "id")
    model = _required_string(payload, "model")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise _invalid_provider_response("DeepSeek response has no completion choice")

    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise _invalid_provider_response("DeepSeek completion has no message")
    content = message.get("content")
    if not isinstance(content, str):
        raise _invalid_provider_response("DeepSeek completion content must be a string")
    if not content.strip():
        raise LLMProviderError(
            "empty-output",
            "DeepSeek returned empty completion content",
            retryable=True,
        )

    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and (
        not isinstance(finish_reason, str) or not finish_reason.strip()
    ):
        raise _invalid_provider_response("DeepSeek finish_reason must be a non-empty string")

    return ProviderResponse(
        output_text=content,
        model=model,
        request_id=request_id,
        finish_reason=finish_reason,
        token_usage=_parse_usage(payload.get("usage")),
    )


def _parse_usage(raw_usage: object) -> GenerationTokenUsage:
    if raw_usage is None:
        return GenerationTokenUsage()
    if not isinstance(raw_usage, dict):
        raise _invalid_provider_response("DeepSeek usage must be a JSON object")

    details = raw_usage.get("completion_tokens_details")
    if details is not None and not isinstance(details, dict):
        raise _invalid_provider_response("DeepSeek completion_tokens_details must be a JSON object")

    return GenerationTokenUsage(
        input_tokens=_optional_token_count(raw_usage, "prompt_tokens"),
        output_tokens=_optional_token_count(raw_usage, "completion_tokens"),
        total_tokens=_optional_token_count(raw_usage, "total_tokens"),
        cached_input_tokens=_optional_token_count(raw_usage, "prompt_cache_hit_tokens"),
        reasoning_tokens=(
            _optional_token_count(details, "reasoning_tokens")
            if isinstance(details, dict)
            else None
        ),
    )


def _required_string(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_provider_response(f"DeepSeek response {field} must be a non-empty string")
    return value


def _optional_token_count(payload: dict[str, object], field: str) -> int | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _invalid_provider_response(f"DeepSeek usage {field} must be a non-negative integer")
    return value


def _invalid_provider_response(message: str) -> LLMProviderError:
    return LLMProviderError(
        "invalid-provider-response",
        message,
        retryable=True,
    )
