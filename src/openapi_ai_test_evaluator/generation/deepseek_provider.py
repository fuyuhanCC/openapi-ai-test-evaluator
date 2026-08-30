"""DeepSeek chat-completion adapter implemented with HTTPX."""

from __future__ import annotations

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
                f"{self._base_url}/responses",
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
    return {
        "model": request.model,
        "instructions": request.system_prompt,
        "input": request.user_prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "test_case_batch",
                "schema": request.response_schema,
            }
        },
        "reasoning": {"effort": "none"},
        "temperature": request.temperature,
        "max_output_tokens": request.max_output_tokens,
        "stream": False,
    }


def _parse_response(payload: object) -> ProviderResponse:
    if not isinstance(payload, dict):
        raise _invalid_provider_response("DeepSeek response must be a JSON object")

    request_id = _required_string(payload, "id")
    model = _required_string(payload, "model")
    if payload.get("object") != "response":
        raise _invalid_provider_response("DeepSeek response object must equal 'response'")
    status = _required_string(payload, "status")
    if status == "failed":
        raise LLMProviderError(
            "provider-response-failed",
            "DeepSeek Responses API reported a failed response",
            retryable=True,
        )
    if status not in {"completed", "incomplete"}:
        raise _invalid_provider_response(f"DeepSeek response has unsupported status {status!r}")

    output = payload.get("output")
    if not isinstance(output, list):
        raise _invalid_provider_response("DeepSeek response output must be a list")
    content_parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise _invalid_provider_response("DeepSeek message content must be a list")
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            text = part.get("text")
            if not isinstance(text, str):
                raise _invalid_provider_response("DeepSeek output_text must contain text")
            content_parts.append(text)
    output_text = "".join(content_parts)
    if not output_text.strip():
        raise LLMProviderError(
            "empty-output",
            "DeepSeek returned empty completion content",
            retryable=True,
        )

    finish_reason = "stop"
    if status == "incomplete":
        incomplete_details = payload.get("incomplete_details")
        if not isinstance(incomplete_details, dict):
            raise _invalid_provider_response(
                "incomplete DeepSeek response requires incomplete_details"
            )
        reason = _required_string(incomplete_details, "reason")
        finish_reason = f"incomplete:{reason}"

    return ProviderResponse(
        output_text=output_text,
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

    input_details = raw_usage.get("input_tokens_details")
    if input_details is not None and not isinstance(input_details, dict):
        raise _invalid_provider_response("DeepSeek input_tokens_details must be a JSON object")
    output_details = raw_usage.get("output_tokens_details")
    if output_details is not None and not isinstance(output_details, dict):
        raise _invalid_provider_response("DeepSeek output_tokens_details must be a JSON object")

    return GenerationTokenUsage(
        input_tokens=_optional_token_count(raw_usage, "input_tokens"),
        output_tokens=_optional_token_count(raw_usage, "output_tokens"),
        total_tokens=_optional_token_count(raw_usage, "total_tokens"),
        cached_input_tokens=(
            _optional_token_count(input_details, "cached_tokens")
            if isinstance(input_details, dict)
            else None
        ),
        reasoning_tokens=(
            _optional_token_count(output_details, "reasoning_tokens")
            if isinstance(output_details, dict)
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
