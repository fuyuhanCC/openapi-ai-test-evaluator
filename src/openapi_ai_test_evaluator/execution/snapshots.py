"""Create sanitized RunResult snapshots from transport-layer values."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.execution import (
    BodySnapshot,
    QueryParameterSnapshot,
    RequestSnapshot,
    ResponseSnapshot,
)
from openapi_ai_test_evaluator.execution.request_builder import PreparedRequest
from openapi_ai_test_evaluator.execution.transport import TransportResponse

REDACTED_VALUE = "[REDACTED]"
UNPARSEABLE_JSON_VALUE = "[UNPARSEABLE JSON BODY OMITTED]"
NON_JSON_VALUE = "[NON-JSON BODY OMITTED]"
_SENSITIVE_NAMES = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "clientsecret",
        "cookie",
        "idtoken",
        "password",
        "passwd",
        "proxyauthorization",
        "refreshtoken",
        "secret",
        "sessiontoken",
        "setcookie",
        "token",
        "xapikey",
        "xauthtoken",
    }
)


def build_request_snapshot(request: PreparedRequest) -> RequestSnapshot:
    """Create a safe artifact snapshot from a resolved logical request."""
    media_type = _request_media_type(request)
    if request.json_body is None:
        body = BodySnapshot(
            media_type=None,
            value=None,
            size_bytes=0,
            truncated=False,
        )
    else:
        body = BodySnapshot(
            media_type=media_type,
            value=sanitize_json_value(request.json_body),
            size_bytes=len(_canonical_json_bytes(request.json_body)),
            truncated=False,
        )

    headers = _sanitize_headers(request.headers.items())
    if request.json_body is not None and "content-type" not in headers:
        headers["content-type"] = media_type or "application/json"

    return RequestSnapshot(
        method=request.method,
        path=request.path,
        query=[
            QueryParameterSnapshot(
                name=name,
                value=REDACTED_VALUE if is_sensitive_name(name) else value,
            )
            for name, value in request.query
        ],
        headers=headers,
        body=body,
    )


def build_response_snapshot(response: TransportResponse) -> ResponseSnapshot:
    """Parse and sanitize a bounded response for artifact storage."""
    media_type = _response_media_type(response.headers)
    value = _decode_response_body(response.body, media_type)
    return ResponseSnapshot(
        status_code=response.status_code,
        headers=_sanitize_headers(response.headers),
        body=BodySnapshot(
            media_type=media_type,
            value=sanitize_json_value(value),
            size_bytes=len(response.body),
            truncated=False,
        ),
    )


def _canonical_json_bytes(value: JsonValue) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _request_media_type(request: PreparedRequest) -> str | None:
    for name, value in request.headers.items():
        if name.casefold() == "content-type":
            return _normalized_media_type(value)
    return "application/json" if request.json_body is not None else None


def _response_media_type(headers: Iterable[tuple[str, str]]) -> str | None:
    for name, value in headers:
        if name.casefold() == "content-type":
            return _normalized_media_type(value)
    return None


def _normalized_media_type(value: str) -> str | None:
    media_type = value.split(";", 1)[0].strip().casefold()
    return media_type or None


def _decode_response_body(body: bytes, media_type: str | None) -> JsonValue:
    if not body:
        return None
    if media_type == "application/json" or (
        media_type is not None and media_type.endswith("+json")
    ):
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return UNPARSEABLE_JSON_VALUE
    return NON_JSON_VALUE


def _sanitize_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for name, value in headers:
        normalized_name = name.casefold()
        safe_value = REDACTED_VALUE if is_sensitive_name(name) else value
        if normalized_name in sanitized and sanitized[normalized_name] != safe_value:
            sanitized[normalized_name] = f"{sanitized[normalized_name]}, {safe_value}"
        else:
            sanitized[normalized_name] = safe_value
    return sanitized


def sanitize_json_value(value: JsonValue) -> JsonValue:
    """Recursively redact values stored under known sensitive field names."""
    if isinstance(value, dict):
        return {
            key: REDACTED_VALUE if is_sensitive_name(key) else sanitize_json_value(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [sanitize_json_value(nested) for nested in value]
    return value


def is_sensitive_name(name: str) -> bool:
    """Return whether a header, parameter, or JSON field name is sensitive."""
    normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
    return normalized in _SENSITIVE_NAMES
