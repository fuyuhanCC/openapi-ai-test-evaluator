import json
from pathlib import Path

from openapi_ai_test_evaluator.generation import build_openapi_context
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]
OPENAPI_30 = ROOT / "examples" / "demo-items" / "openapi.yaml"
OPENAPI_31 = ROOT / "examples" / "demo-items" / "openapi-3.1.yaml"


def test_builds_deterministic_context_from_normalized_openapi() -> None:
    spec = load_openapi(OPENAPI_30)

    first = build_openapi_context(spec)
    second = build_openapi_context(spec)

    assert first == second
    assert first["description"] == "A deterministic fixture API used to test OATE itself."
    assert [operation["operation_id"] for operation in first["operations"]] == [
        "createItem",
        "deleteItem",
        "getItem",
        "listItems",
        "replaceItem",
        "updateItem",
    ]


def test_keeps_request_constraints_and_response_schemas() -> None:
    context = build_openapi_context(load_openapi(OPENAPI_30))
    operations = {operation["operation_id"]: operation for operation in context["operations"]}

    create_item = operations["createItem"]
    assert create_item["method"] == "POST"
    assert create_item["path"] == "/items"
    assert create_item["summary"] == "Create an item"
    assert create_item["request_body"] == {
        "required": True,
        "schema": {"$ref": "#/components/schemas/ItemCreate"},
    }
    assert create_item["responses"]["201"] == {
        "schema": {"$ref": "#/components/schemas/Item"},
        "description": "Item created",
    }

    list_parameters = {
        parameter["name"]: parameter for parameter in operations["listItems"]["parameters"]
    }
    assert list_parameters["limit"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 20,
    }


def test_includes_only_transitively_referenced_schema_components(tmp_path: Path) -> None:
    spec_path = tmp_path / "openapi.yaml"
    spec_path.write_text(
        """\
openapi: 3.0.3
info:
  title: Reference API
  version: 1.0.0
servers:
  - url: https://api.example.test/private
paths:
  /used:
    get:
      operationId: getUsed
      responses:
        "200":
          description: Used response
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Used"
components:
  schemas:
    Used:
      type: object
      x-internal-note: remove-me
      properties:
        nested:
          $ref: "#/components/schemas/Nested"
    Nested:
      type: string
    Unused:
      type: string
""",
        encoding="utf-8",
    )

    context = build_openapi_context(load_openapi(spec_path))

    assert set(context["referenced_schemas"]) == {
        "#/components/schemas/Nested",
        "#/components/schemas/Used",
    }
    assert "x-internal-note" not in context["referenced_schemas"]["#/components/schemas/Used"]
    serialized = json.dumps(context)
    assert "servers" not in serialized
    assert "api.example.test" not in serialized
    assert "Unused" not in serialized


def test_preserves_openapi_31_schema_semantics() -> None:
    context = build_openapi_context(load_openapi(OPENAPI_31))
    item_create = context["referenced_schemas"]["#/components/schemas/ItemCreate"]

    assert item_create["properties"]["category"]["type"] == ["string", "null"]
