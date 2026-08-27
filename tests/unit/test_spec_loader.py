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


def test_demo_contract_declares_validation_errors_for_parameterized_operations() -> None:
    spec = load_openapi(DEMO_SPEC)

    for operation_id in ("listItems", "getItem", "replaceItem", "updateItem", "deleteItem"):
        response = spec.operations[operation_id].responses["400"]
        assert response.schema_definition == {"$ref": "#/components/schemas/Error"}


def test_loads_openapi_31_and_preserves_its_schema(tmp_path: Path) -> None:
    spec_path = tmp_path / "openapi.yaml"
    spec_path.write_text(
        """\
openapi: 3.1.0
info:
  title: Future API
  version: 1.0.0
paths:
  /message:
    get:
      operationId: getMessage
      responses:
        "200":
          description: A nullable message
          content:
            application/json:
              schema:
                type: [string, "null"]
        "201":
          description: Any JSON value
          content:
            application/json:
              schema: true
        default:
          description: No JSON value
          content:
            application/json:
              schema: false
""",
        encoding="utf-8",
    )

    spec = load_openapi(spec_path)

    assert spec.openapi_version == "3.1.0"
    assert spec.operations["getMessage"].responses["200"].schema_definition == {
        "type": ["string", "null"]
    }
    assert spec.operations["getMessage"].responses["201"].schema_definition is True
    assert spec.operations["getMessage"].responses["default"].schema_definition is False


def test_rejects_unsupported_openapi_31_schema_dialect(tmp_path: Path) -> None:
    spec_path = tmp_path / "custom-dialect.yaml"
    spec_path.write_text(
        """\
openapi: 3.1.0
jsonSchemaDialect: https://example.com/custom-dialect
info:
  title: Custom Dialect API
  version: 1.0.0
paths: {}
""",
        encoding="utf-8",
    )

    with pytest.raises(SpecLoadError, match="unsupported jsonSchemaDialect"):
        load_openapi(spec_path)


@pytest.mark.parametrize(
    "dialect",
    [
        "https://spec.openapis.org/oas/3.1/dialect/base",
        "https://json-schema.org/draft/2020-12/schema",
    ],
)
def test_accepts_supported_openapi_31_schema_dialects(tmp_path: Path, dialect: str) -> None:
    spec_path = tmp_path / "supported-dialect.yaml"
    spec_path.write_text(
        f"""\
openapi: 3.1.0
jsonSchemaDialect: {dialect}
info:
  title: Supported Dialect API
  version: 1.0.0
paths: {{}}
""",
        encoding="utf-8",
    )

    assert load_openapi(spec_path).openapi_version == "3.1.0"


def test_marks_unsupported_schema_features_on_their_operation(tmp_path: Path) -> None:
    spec_path = tmp_path / "advanced-schema.yaml"
    spec_path.write_text(
        """\
openapi: 3.1.0
info:
  title: Advanced Schema API
  version: 1.0.0
paths:
  /conditional:
    post:
      operationId: createConditional
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                host:
                  type: string
                  format: hostname
              if:
                required: [host]
              then:
                minProperties: 1
              unevaluatedProperties: false
      responses:
        "204":
          description: Created
""",
        encoding="utf-8",
    )

    operation = load_openapi(spec_path).operations["createConditional"]

    assert any("'if'" in reason for reason in operation.unsupported_reasons)
    assert any("'then'" in reason for reason in operation.unsupported_reasons)
    assert any("'unevaluatedProperties'" in reason for reason in operation.unsupported_reasons)
    assert any("hostname" in reason for reason in operation.unsupported_reasons)
    assert all("request body schema" in reason for reason in operation.unsupported_reasons)


def test_accepts_supported_schema_level_dialect_override(tmp_path: Path) -> None:
    spec_path = tmp_path / "supported-schema-override.yaml"
    spec_path.write_text(
        """\
openapi: 3.1.0
info:
  title: Schema Override API
  version: 1.0.0
paths:
  /supported:
    get:
      operationId: getSupported
      responses:
        "200":
          description: Supported dialect
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Supported"
components:
  schemas:
    Supported:
      $schema: https://json-schema.org/draft/2020-12/schema
      type: object
""",
        encoding="utf-8",
    )

    spec = load_openapi(spec_path)

    assert spec.operations["getSupported"].unsupported_reasons == []


def test_rejects_unsupported_schema_level_dialect_override(tmp_path: Path) -> None:
    spec_path = tmp_path / "custom-schema-override.yaml"
    spec_path.write_text(
        """\
openapi: 3.1.0
info:
  title: Custom Schema Override API
  version: 1.0.0
paths: {}
components:
  schemas:
    Custom:
      $schema: https://example.com/company/custom-dialect
      type: object
""",
        encoding="utf-8",
    )

    with pytest.raises(SpecLoadError, match="Unknown JSON Schema dialect"):
        load_openapi(spec_path)


def test_resolves_escaped_local_reference_tokens() -> None:
    document = {"components": {"schemas": {"a/b~c": {"type": "string"}}}}

    assert resolve_local_ref(document, "#/components/schemas/a~1b~0c") == {"type": "string"}


def test_allows_openapi_31_reference_summary_and_description() -> None:
    document = {
        "openapi": "3.1.0",
        "components": {"responses": {"Success": {"description": "Original"}}},
    }

    resolved = resolve_reference_object(
        document,
        {
            "$ref": "#/components/responses/Success",
            "summary": "Success response",
            "description": "Usage-specific annotation",
        },
    )

    assert resolved == {"description": "Original"}


def test_rejects_other_openapi_31_reference_siblings() -> None:
    document = {
        "openapi": "3.1.0",
        "components": {"responses": {"Success": {"description": "Original"}}},
    }

    with pytest.raises(SpecLoadError, match="sibling keys"):
        resolve_reference_object(
            document,
            {"$ref": "#/components/responses/Success", "x-extra": True},
        )


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
