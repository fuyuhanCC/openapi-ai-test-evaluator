"""Capture public Schemathesis Case fields into a third-party-neutral snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from schemathesis import Case
from schemathesis.core import NOT_SET

from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.generation.schemathesis_adapter import (
    AdaptationRejection,
    AdaptationRejectionCode,
    CapturedGenerationMode,
    CapturedPhase,
    CapturedSchemathesisCase,
)


@dataclass(frozen=True, slots=True)
class SchemathesisCaseCapture:
    captured: CapturedSchemathesisCase | None
    rejections: tuple[AdaptationRejection, ...]

    @property
    def succeeded(self) -> bool:
        return self.captured is not None


def capture_schemathesis_case(
    case: Case,
    spec: OpenAPISpec,
    *,
    case_id: str,
) -> SchemathesisCaseCapture:
    """Copy one generated Case without retaining Schemathesis-owned objects."""
    operation_id = _operation_id(case, spec)
    if operation_id is None:
        return _rejected(
            AdaptationRejectionCode.CAPTURE_OPERATION_UNRESOLVED,
            "captured Schemathesis operation could not be mapped to the loaded OpenAPI",
        )

    mode_value = _nested_enum_value(case, "meta", "generation", "mode")
    try:
        mode = CapturedGenerationMode(mode_value)
    except ValueError:
        return _rejected(
            AdaptationRejectionCode.CAPTURE_MODE_UNSUPPORTED,
            f"Schemathesis generation mode {mode_value!r} is unsupported",
        )

    phase_value = _nested_enum_value(case, "meta", "phase", "name")
    try:
        phase = CapturedPhase(phase_value)
    except ValueError:
        return _rejected(
            AdaptationRejectionCode.CAPTURE_PHASE_UNSUPPORTED,
            f"Schemathesis phase {phase_value!r} is outside the primary stateless baseline",
        )

    components: dict[str, tuple[tuple[str, object], ...]] = {}
    for name in ("path_parameters", "query", "headers", "cookies"):
        component = _component_pairs(getattr(case, name, None))
        if component is None:
            return _rejected(
                AdaptationRejectionCode.CAPTURE_COMPONENT_UNSUPPORTED,
                f"Schemathesis {name} is not a string-keyed mapping",
                detail_code=name,
            )
        components[name] = component

    method = getattr(case, "method", None)
    path = getattr(case, "path", None)
    if not isinstance(method, str) or not isinstance(path, str):
        return _rejected(
            AdaptationRejectionCode.CAPTURE_COMPONENT_UNSUPPORTED,
            "Schemathesis method and path must be strings",
            detail_code="request_target",
        )

    body = getattr(case, "body", NOT_SET)
    body_present = body is not NOT_SET
    media_type = getattr(case, "media_type", None)
    if media_type is not None and not isinstance(media_type, str):
        return _rejected(
            AdaptationRejectionCode.CAPTURE_COMPONENT_UNSUPPORTED,
            "Schemathesis media_type must be a string or null",
            detail_code="media_type",
        )

    return SchemathesisCaseCapture(
        captured=CapturedSchemathesisCase(
            case_id=case_id,
            operation_id=operation_id,
            method=method,
            path=path,
            mode=mode,
            phase=phase,
            path_parameters=components["path_parameters"],
            query=components["query"],
            headers=components["headers"],
            cookies=components["cookies"],
            body_present=body_present,
            body=None if not body_present else body,
            media_type=media_type,
        ),
        rejections=(),
    )


def _operation_id(case: Case, spec: OpenAPISpec) -> str | None:
    operation = getattr(case, "operation", None)
    definition = getattr(operation, "definition", None)
    raw_definition = getattr(definition, "raw", None)
    if isinstance(raw_definition, dict):
        raw_operation_id = raw_definition.get("operationId")
        if isinstance(raw_operation_id, str) and raw_operation_id in spec.operations:
            return raw_operation_id

    declared_method = getattr(operation, "method", None)
    declared_path = getattr(operation, "path", None)
    if not isinstance(declared_method, str) or not isinstance(declared_path, str):
        return None
    matches = [
        operation_id
        for operation_id, candidate in spec.operations.items()
        if candidate.method.casefold() == declared_method.casefold()
        and candidate.path == declared_path
    ]
    return matches[0] if len(matches) == 1 else None


def _nested_enum_value(value: object, *attributes: str) -> Any:
    current = value
    for attribute in attributes:
        current = getattr(current, attribute, None)
    return getattr(current, "value", current)


def _component_pairs(value: object) -> tuple[tuple[str, object], ...] | None:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        return None
    pairs = tuple(value.items())
    if not all(isinstance(name, str) for name, _ in pairs):
        return None
    return pairs


def _rejected(
    code: AdaptationRejectionCode,
    message: str,
    *,
    detail_code: str | None = None,
) -> SchemathesisCaseCapture:
    return SchemathesisCaseCapture(
        captured=None,
        rejections=(
            AdaptationRejection(
                code=code,
                message=message,
                detail_code=detail_code,
            ),
        ),
    )
