from pathlib import Path

import pytest

from openapi_ai_test_evaluator.domain.openapi import ParameterLocation
from openapi_ai_test_evaluator.spec import SpecLoadError, load_openapi
from openapi_ai_test_evaluator.spec.loader import resolve_local_ref, resolve_reference_object

ROOT = Path(__file__).parents[2]
DEMO_SPEC = ROOT / "examples" / "demo-items" / "openapi.yaml"


def test_loads_and_normalizes_demo_openapi() -> None:
    spec = load_openapi(DEMO_SPEC)

    assert spec.spec_id == "demo-items-v1"
    assert spec.openapi_version == "3.0.3"
    assert set(spec.operations) == {
        "listItems",
        "createItem",
        "getItem",
        "replaceItem",
        "updateItem",
        "deleteItem",
    }


def test_inherits_path_parameters_and_resolves_response_references() -> None:
    spec = load_openapi(DEMO_SPEC)
    operation = spec.operations["getItem"]

    item_id = operation.parameter(ParameterLocation.PATH, "itemId")
    assert item_id is not None
    assert item_id.required is True
    assert item_id.schema_definition == {"type": "integer", "minimum": 1}
    assert operation.responses["404"].schema_definition == {"$ref": "#/components/schemas/Error"}


def test_rejects_openapi_31_for_v1(tmp_path: Path) -> None:
    spec_path = tmp_path / "openapi.yaml"
    spec_path.write_text(
        """\
openapi: 3.1.0
info:
  title: Future API
  version: 1.0.0
paths: {}
""",
        encoding="utf-8",
    )

    with pytest.raises(SpecLoadError, match="supports OpenAPI 3.0.x"):
        load_openapi(spec_path)


def test_resolves_escaped_local_reference_tokens() -> None:
    document = {"components": {"schemas": {"a/b~c": {"type": "string"}}}}

    assert resolve_local_ref(document, "#/components/schemas/a~1b~0c") == {"type": "string"}


def test_rejects_external_missing_and_ambiguous_references() -> None:
    document = {"components": {"schemas": {}}}

    with pytest.raises(SpecLoadError, match="external references"):
        resolve_local_ref(document, "other.yaml#/Thing")
    with pytest.raises(SpecLoadError, match="unresolvable"):
        resolve_local_ref(document, "#/components/schemas/Missing")
    with pytest.raises(SpecLoadError, match="sibling keys"):
        resolve_reference_object(
            document,
            {"$ref": "#/components/schemas/Missing", "description": "ambiguous"},
        )


def test_rejects_non_mapping_openapi_yaml(tmp_path: Path) -> None:
    spec_path = tmp_path / "openapi.yaml"
    spec_path.write_text("- not\n- an\n- object\n", encoding="utf-8")

    with pytest.raises(SpecLoadError, match="YAML mapping"):
        load_openapi(spec_path)


def test_reports_missing_and_malformed_openapi_files(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.yaml"
    malformed_path = tmp_path / "malformed.yaml"
    malformed_path.write_text("openapi: [\n", encoding="utf-8")

    with pytest.raises(SpecLoadError, match="cannot read"):
        load_openapi(missing_path)
    with pytest.raises(SpecLoadError, match="invalid YAML"):
        load_openapi(malformed_path)


def test_rejects_document_that_is_not_valid_openapi(tmp_path: Path) -> None:
    spec_path = tmp_path / "invalid-openapi.yaml"
    spec_path.write_text("openapi: 3.0.3\ninfo: {}\npaths: {}\n", encoding="utf-8")

    with pytest.raises(SpecLoadError, match="invalid OpenAPI document"):
        load_openapi(spec_path)
