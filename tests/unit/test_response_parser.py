import pytest

from openapi_ai_test_evaluator.execution import (
    ResponseBodyKind,
    ResponseParseError,
    TransportResponse,
    parse_response,
)


def response(
    *,
    body: bytes,
    content_type: str | None = None,
) -> TransportResponse:
    headers = (("Content-Type", content_type),) if content_type is not None else ()
    return TransportResponse(
        status_code=200,
        headers=headers,
        body=body,
        duration_ms=7,
    )


def test_parses_json_without_redacting_runtime_value() -> None:
    raw = response(
        body=b'{"id":1,"token":"runtime-secret"}',
        content_type="application/json; charset=utf-8",
    )

    parsed = parse_response(raw)

    assert parsed.status_code == 200
    assert parsed.media_type == "application/json"
    assert parsed.body_kind is ResponseBodyKind.JSON
    assert parsed.body == {"id": 1, "token": "runtime-secret"}
    assert parsed.duration_ms == 7


def test_parses_structured_json_media_type() -> None:
    parsed = parse_response(
        response(
            body=b'{"title":"Invalid request"}',
            content_type="application/problem+json",
        )
    )

    assert parsed.body_kind is ResponseBodyKind.JSON
    assert parsed.body == {"title": "Invalid request"}


def test_keeps_empty_body_distinct_from_json_null() -> None:
    empty = parse_response(response(body=b"", content_type="application/json"))
    json_null = parse_response(response(body=b"null", content_type="application/json"))

    assert empty.body_kind is ResponseBodyKind.EMPTY
    assert empty.body is None
    assert json_null.body_kind is ResponseBodyKind.JSON
    assert json_null.body is None


def test_decodes_text_using_declared_charset() -> None:
    parsed = parse_response(
        response(body="café".encode("iso-8859-1"), content_type="text/plain; charset=iso-8859-1")
    )

    assert parsed.body_kind is ResponseBodyKind.TEXT
    assert parsed.body == "café"


def test_treats_undeclared_content_type_as_binary() -> None:
    body = b"\x00\x01\x02"

    parsed = parse_response(response(body=body))

    assert parsed.media_type is None
    assert parsed.body_kind is ResponseBodyKind.BINARY
    assert parsed.body == body


@pytest.mark.parametrize("body", [b'{"broken":', b'{"value": NaN}'])
def test_rejects_invalid_or_nonstandard_json(body: bytes) -> None:
    with pytest.raises(ResponseParseError, match="contains invalid JSON") as caught:
        parse_response(response(body=body, content_type="application/json"))

    assert caught.value.location == "response.body"


def test_rejects_text_that_does_not_match_declared_charset() -> None:
    with pytest.raises(ResponseParseError, match="not valid text"):
        parse_response(response(body=b"\xff", content_type="text/plain; charset=utf-8"))


def test_rejects_unknown_text_charset() -> None:
    with pytest.raises(ResponseParseError, match="unsupported charset"):
        parse_response(response(body=b"hello", content_type="text/plain; charset=not-real"))
