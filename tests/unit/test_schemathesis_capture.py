import warnings
from pathlib import Path
from types import SimpleNamespace

import schemathesis
from schemathesis.core import NOT_SET

from openapi_ai_test_evaluator.generation import (
    AdaptationRejectionCode,
    CapturedGenerationMode,
    CapturedPhase,
    adapt_schemathesis_case,
    capture_schemathesis_case,
)
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]
SPEC_PATH = ROOT / "examples" / "demo-items" / "openapi.yaml"
SPEC = load_openapi(SPEC_PATH)


def fake_case(**changes: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "operation": SimpleNamespace(
            definition=SimpleNamespace(raw={"operationId": "listItems"}),
            method="get",
            path="/items",
        ),
        "method": "GET",
        "path": "/items",
        "path_parameters": {},
        "query": {"limit": 20},
        "headers": {},
        "cookies": {},
        "body": NOT_SET,
        "media_type": None,
        "meta": SimpleNamespace(
            generation=SimpleNamespace(mode=SimpleNamespace(value="positive")),
            phase=SimpleNamespace(name=SimpleNamespace(value="coverage")),
        ),
    }
    values.update(changes)
    return SimpleNamespace(**values)


def first_rejection_code(capture) -> AdaptationRejectionCode:
    assert capture.captured is None
    assert capture.rejections
    return capture.rejections[0].code


def test_captures_public_case_fields_without_retaining_component_mappings() -> None:
    query = {"limit": 20}
    capture = capture_schemathesis_case(
        fake_case(query=query),  # type: ignore[arg-type]
        SPEC,
        case_id="schemathesis-1",
    )
    query["limit"] = 99

    assert capture.succeeded is True
    assert capture.rejections == ()
    assert capture.captured is not None
    assert capture.captured.operation_id == "listItems"
    assert capture.captured.mode is CapturedGenerationMode.POSITIVE
    assert capture.captured.phase is CapturedPhase.COVERAGE
    assert capture.captured.query == (("limit", 20),)
    assert capture.captured.body_present is False
    assert capture.captured.body is None


def test_distinguishes_explicit_json_null_from_schemathesis_not_set() -> None:
    capture = capture_schemathesis_case(
        fake_case(
            operation=SimpleNamespace(
                definition=SimpleNamespace(raw={"operationId": "createItem"}),
                method="post",
                path="/items",
            ),
            method="POST",
            query={},
            body=None,
            media_type="application/json",
            meta=SimpleNamespace(
                generation=SimpleNamespace(mode=SimpleNamespace(value="negative")),
                phase=SimpleNamespace(name=SimpleNamespace(value="fuzzing")),
            ),
        ),  # type: ignore[arg-type]
        SPEC,
        case_id="schemathesis-2",
    )

    assert capture.captured is not None
    assert capture.captured.body_present is True
    assert capture.captured.body is None
    assert capture.captured.mode is CapturedGenerationMode.NEGATIVE
    assert capture.captured.phase is CapturedPhase.FUZZING


def test_falls_back_to_declared_method_and_path_when_operation_id_is_absent() -> None:
    capture = capture_schemathesis_case(
        fake_case(
            operation=SimpleNamespace(
                definition=SimpleNamespace(raw={}),
                method="get",
                path="/items",
            )
        ),  # type: ignore[arg-type]
        SPEC,
        case_id="schemathesis-3",
    )

    assert capture.captured is not None
    assert capture.captured.operation_id == "listItems"


def test_rejects_stateful_phase_from_the_primary_stateless_capture() -> None:
    capture = capture_schemathesis_case(
        fake_case(
            meta=SimpleNamespace(
                generation=SimpleNamespace(mode=SimpleNamespace(value="positive")),
                phase=SimpleNamespace(name=SimpleNamespace(value="stateful")),
            )
        ),  # type: ignore[arg-type]
        SPEC,
        case_id="schemathesis-4",
    )

    assert first_rejection_code(capture) is AdaptationRejectionCode.CAPTURE_PHASE_UNSUPPORTED


def test_rejects_non_mapping_request_components() -> None:
    capture = capture_schemathesis_case(
        fake_case(query=[("limit", 20)]),  # type: ignore[arg-type]
        SPEC,
        case_id="schemathesis-5",
    )

    assert first_rejection_code(capture) is AdaptationRejectionCode.CAPTURE_COMPONENT_UNSUPPORTED
    assert capture.rejections[0].detail_code == "query"


def test_captures_and_adapts_a_real_schemathesis_case() -> None:
    schema = schemathesis.openapi.from_path(SPEC_PATH)
    operation = schema["/items"]["GET"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        generated = operation.as_strategy(
            generation_mode=schemathesis.GenerationMode.POSITIVE
        ).example()
    case = operation.Case(query={"limit": 20}, _meta=generated.meta)

    capture = capture_schemathesis_case(case, SPEC, case_id="schemathesis-real-1")

    assert capture.captured is not None
    adaptation = adapt_schemathesis_case(capture.captured, SPEC)
    assert adaptation.succeeded is True
