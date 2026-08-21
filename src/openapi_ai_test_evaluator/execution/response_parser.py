"""Parse bounded transport responses for assertions and extractions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from email.message import Message
from enum import StrEnum

from pydantic import JsonValue

from openapi_ai_test_evaluator.execution.transport import TransportResponse


class ResponseBodyKind(StrEnum):
    """How the response body was interpreted from its declared media type."""

    EMPTY = "empty"
    JSON = "json"
    TEXT = "text"
    BINARY = "binary"


class ResponseParseError(ValueError):
    """A declared textual response body could not be parsed deterministically."""

    def __init__(self, message: str) -> None:
        self.location = "response.body"
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ResponseData:
    """In-memory response value used by assertions and extractions."""

    status_code: int
    headers: tuple[tuple[str, str], ...]
    media_type: str | None
    body_kind: ResponseBodyKind
    body: JsonValue | str | bytes | None
    duration_ms: int


def parse_response(response: TransportResponse) -> ResponseData:
    """Interpret a raw bounded response without redacting or changing its value."""
    media_type, charset = _content_type(response.headers)

    if not response.body:
        body_kind = ResponseBodyKind.EMPTY
        body: JsonValue | str | bytes | None = None
    elif _is_json_media_type(media_type):
        body_kind = ResponseBodyKind.JSON
        body = _parse_json(response.body)
    elif media_type is not None and media_type.startswith("text/"):
        body_kind = ResponseBodyKind.TEXT
        body = _parse_text(response.body, charset)
    else:
        body_kind = ResponseBodyKind.BINARY
        body = response.body

    return ResponseData(
        status_code=response.status_code,
        headers=response.headers,
        media_type=media_type,
        body_kind=body_kind,
        body=body,
        duration_ms=response.duration_ms,
    )


def _content_type(headers: tuple[tuple[str, str], ...]) -> tuple[str | None, str | None]:
    for name, value in headers:
        if name.casefold() != "content-type":
            continue
        message = Message()
        message["content-type"] = value
        media_type = message.get_content_type().casefold()
        charset = message.get_content_charset()
        return media_type, charset.casefold() if charset is not None else None
    return None, None


def _is_json_media_type(media_type: str | None) -> bool:
    return media_type == "application/json" or (
        media_type is not None and media_type.endswith("+json")
    )


def _parse_json(body: bytes) -> JsonValue:
    try:
        return json.loads(body, parse_constant=_reject_nonstandard_json_number)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ResponseParseError("response declares JSON but contains invalid JSON") from error


def _reject_nonstandard_json_number(value: str) -> None:
    raise json.JSONDecodeError(f"non-standard JSON number {value}", value, 0)


def _parse_text(body: bytes, charset: str | None) -> str:
    encoding = charset or "utf-8"
    try:
        return body.decode(encoding)
    except LookupError as error:
        raise ResponseParseError(f"response declares unsupported charset {encoding!r}") from error
    except UnicodeDecodeError as error:
        raise ResponseParseError(
            f"response body is not valid text for declared charset {encoding!r}"
        ) from error
