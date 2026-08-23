"""Deterministic in-memory provider used by tests and offline development."""

from __future__ import annotations

from openapi_ai_test_evaluator.generation.provider import (
    LLMProviderError,
    ProviderRequest,
    ProviderResponse,
)


class FakeProvider:
    """Return one configured response or failure while recording every request."""

    def __init__(
        self,
        *,
        response: ProviderResponse | None = None,
        error: LLMProviderError | None = None,
        name: str = "fake",
    ) -> None:
        if (response is None) == (error is None):
            raise ValueError("configure exactly one fake provider response or error")
        if not name.strip():
            raise ValueError("fake provider name cannot be empty")
        self._name = name
        self._response = response
        self._error = error
        self._requests: list[ProviderRequest] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def requests(self) -> tuple[ProviderRequest, ...]:
        return tuple(request.model_copy(deep=True) for request in self._requests)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self._requests.append(request.model_copy(deep=True))
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response.model_copy(deep=True)
