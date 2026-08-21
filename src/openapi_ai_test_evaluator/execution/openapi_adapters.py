"""Adapt internal execution values to openapi-core protocols."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from openapi_core.datatypes import RequestParameters
from werkzeug.datastructures import Headers, ImmutableMultiDict

from openapi_ai_test_evaluator.domain.openapi import OperationModel
from openapi_ai_test_evaluator.execution.request_builder import PreparedRequest
from openapi_ai_test_evaluator.execution.transport import TransportResponse


@dataclass(frozen=True, slots=True)
class OpenAPIRequestAdapter:
    """An internal prepared request exposed through openapi-core's protocol."""

    host_url: str
    path: str
    path_pattern: str
    method: str
    parameters: RequestParameters
    content_type: str
    body: bytes | None


@dataclass(frozen=True, slots=True)
class OpenAPIResponseAdapter:
    """A raw transport response exposed through openapi-core's protocol."""

    status_code: int
    headers: Headers
    content_type: str
    data: bytes


def adapt_openapi_request(
    request: PreparedRequest,
    operation: OperationModel,
    base_url: str,
) -> OpenAPIRequestAdapter:
    """Build an openapi-core request without changing the logical request."""
    if request.operation_id != operation.operation_id:
        raise ValueError("prepared request and OpenAPI operation IDs do not match")

    host_url, path, path_pattern = _target_paths(base_url, request.path, operation.path)
    headers = Headers(request.headers.items())
    if request.json_body is not None and "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"

    return OpenAPIRequestAdapter(
        host_url=host_url,
        path=path,
        path_pattern=path_pattern,
        method=request.method.lower(),
        parameters=RequestParameters(
            query=ImmutableMultiDict(request.query),
            header=headers,
            cookie=ImmutableMultiDict(),
            path=dict(request.path_parameters),
        ),
        content_type=(headers.get("Content-Type") or "").casefold(),
        body=_encode_json_body(request),
    )


def adapt_openapi_response(response: TransportResponse) -> OpenAPIResponseAdapter:
    """Build an openapi-core response while preserving the raw response body."""
    headers = Headers(response.headers)
    return OpenAPIResponseAdapter(
        status_code=response.status_code,
        headers=headers,
        content_type=(headers.get("Content-Type") or "").casefold(),
        data=response.body,
    )


def _target_paths(base_url: str, path: str, path_pattern: str) -> tuple[str, str, str]:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url cannot contain a query string or fragment")

    host_url = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    base_path = parsed.path.rstrip("/")
    return (
        host_url,
        _join_url_path(base_path, path),
        _join_url_path(base_path, path_pattern),
    )


def _join_url_path(base_path: str, path: str) -> str:
    return f"{base_path}/{path.lstrip('/')}" or "/"


def _encode_json_body(request: PreparedRequest) -> bytes | None:
    if request.json_body is None:
        return None
    return json.dumps(
        request.json_body,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
