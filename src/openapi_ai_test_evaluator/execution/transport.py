"""Bounded, deterministic HTTP transport for prepared requests."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter_ns

import httpx

from openapi_ai_test_evaluator.domain.execution import ErrorCategory
from openapi_ai_test_evaluator.execution.request_builder import (
    PreparedRequest,
    encode_json_body,
)

DEFAULT_MAX_RESPONSE_BYTES = 1_048_576


class TransportFailure(RuntimeError):
    """A prepared request did not produce a usable bounded response."""

    def __init__(self, category: ErrorCategory, location: str, message: str) -> None:
        self.category = category
        self.location = location
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class TransportResponse:
    """Raw in-memory response data returned to the execution layer."""

    status_code: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    duration_ms: int


class HttpTransport:
    """Send prepared requests without retries or redirect following."""

    def __init__(
        self,
        base_url: str,
        *,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        normalized_base_url = f"{base_url.rstrip('/')}/"
        self._max_response_bytes = max_response_bytes
        self._client = httpx.Client(
            base_url=normalized_base_url,
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        )

    def __enter__(self) -> HttpTransport:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def send(self, request: PreparedRequest) -> TransportResponse:
        """Send one request and retain at most the configured response bytes."""
        started_at = perf_counter_ns()
        request_path = request.path.lstrip("/")
        timeout_seconds = request.timeout_ms / 1000
        request_body = encode_json_body(request)
        headers = dict(request.headers)
        if request.has_json_body and not any(
            name.casefold() == "content-type" for name in headers
        ):
            headers["Content-Type"] = "application/json"

        try:
            if request_body is None:
                response_context = self._client.stream(
                    request.method,
                    request_path,
                    params=request.query,
                    headers=headers,
                    timeout=timeout_seconds,
                )
            else:
                response_context = self._client.stream(
                    request.method,
                    request_path,
                    params=request.query,
                    headers=headers,
                    content=request_body,
                    timeout=timeout_seconds,
                )

            with response_context as response:
                body = self._read_bounded_body(response)
                return TransportResponse(
                    status_code=response.status_code,
                    headers=tuple(response.headers.multi_items()),
                    body=body,
                    duration_ms=_elapsed_ms(started_at),
                )
        except TransportFailure:
            raise
        except httpx.TimeoutException as error:
            raise TransportFailure(
                ErrorCategory.TIMEOUT,
                "transport",
                "HTTP request timed out",
            ) from error
        except httpx.ConnectError as error:
            raise TransportFailure(
                ErrorCategory.SUT_UNAVAILABLE,
                "transport",
                "could not connect to the system under test",
            ) from error
        except httpx.RequestError as error:
            raise TransportFailure(
                ErrorCategory.TRANSPORT_ERROR,
                "transport",
                f"HTTP transport failed ({type(error).__name__})",
            ) from error

    def _read_bounded_body(self, response: httpx.Response) -> bytes:
        declared_size = response.headers.get("content-length")
        if declared_size is not None:
            try:
                if int(declared_size) > self._max_response_bytes:
                    raise self._response_too_large()
            except ValueError:
                pass

        body = bytearray()
        for chunk in response.iter_bytes():
            if len(body) + len(chunk) > self._max_response_bytes:
                raise self._response_too_large()
            body.extend(chunk)
        return bytes(body)

    def _response_too_large(self) -> TransportFailure:
        return TransportFailure(
            ErrorCategory.RESPONSE_TOO_LARGE,
            "response.body",
            f"response exceeds the {self._max_response_bytes}-byte limit",
        )


def _elapsed_ms(started_at: int) -> int:
    return max(0, (perf_counter_ns() - started_at) // 1_000_000)
