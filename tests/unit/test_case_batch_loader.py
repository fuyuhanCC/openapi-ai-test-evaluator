import json
from pathlib import Path

import pytest

from openapi_ai_test_evaluator.validation import TestCaseBatchLoadError as BatchLoadError
from openapi_ai_test_evaluator.validation import load_test_case_batch, parse_test_case_batch


def batch_data() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "cases": [
            {
                "id": "list-items",
                "steps": [{"id": "list", "operation_id": "listItems"}],
            }
        ],
    }


def test_parses_generated_json_text() -> None:
    batch = parse_test_case_batch(json.dumps(batch_data()))

    assert batch.cases[0].id == "list-items"


def test_parses_explicit_yaml_text() -> None:
    batch = parse_test_case_batch(
        """
schema_version: "1.0"
cases:
  - id: list-items
    steps:
      - id: list
        operation_id: listItems
""",
        document_format="yaml",
    )

    assert batch.cases[0].steps[0].operation_id == "listItems"


@pytest.mark.parametrize("suffix", [".json", ".yaml", ".yml"])
def test_loads_supported_file_formats(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"cases{suffix}"
    if suffix == ".json":
        path.write_text(json.dumps(batch_data()), encoding="utf-8")
    else:
        path.write_text(
            """
schema_version: "1.0"
cases:
  - id: list-items
    steps:
      - id: list
        operation_id: listItems
""",
            encoding="utf-8",
        )

    assert load_test_case_batch(path).cases[0].id == "list-items"


def test_rejects_markdown_wrapped_model_output() -> None:
    wrapped = f"```json\n{json.dumps(batch_data())}\n```"

    with pytest.raises(BatchLoadError, match="invalid JSON"):
        parse_test_case_batch(wrapped)


def test_rejects_non_mapping_top_level() -> None:
    with pytest.raises(BatchLoadError, match="mapping at the top level"):
        parse_test_case_batch("[]")


def test_rejects_structurally_invalid_batch() -> None:
    with pytest.raises(BatchLoadError, match="invalid TestCaseBatch"):
        parse_test_case_batch(json.dumps({"schema_version": "1.0", "cases": []}))


def test_rejects_unsupported_file_suffix(tmp_path: Path) -> None:
    path = tmp_path / "cases.txt"
    path.write_text(json.dumps(batch_data()), encoding="utf-8")

    with pytest.raises(BatchLoadError, match="unsupported TestCaseBatch file suffix"):
        load_test_case_batch(path)
