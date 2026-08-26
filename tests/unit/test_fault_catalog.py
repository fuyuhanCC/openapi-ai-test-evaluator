from pathlib import Path

import pytest

from services.fault_proxy.catalog import FaultCatalogLoadError, load_fault_catalog

PROJECT_ROOT = Path(__file__).parents[2]
DEMO_FAULTS = PROJECT_ROOT / "benchmarks" / "demo_items" / "faults"


def test_loads_demo_fault_catalog_in_filename_order() -> None:
    definitions = load_fault_catalog(DEMO_FAULTS)

    assert [definition.fault_id for definition in definitions] == [
        "get-id-as-string",
        "get-missing-name",
        "get-status-error",
        "list-duplicate-first-item",
    ]


def test_rejects_missing_catalog_directory(tmp_path: Path) -> None:
    with pytest.raises(FaultCatalogLoadError, match="does not exist"):
        load_fault_catalog(tmp_path / "missing")


def test_rejects_empty_catalog_directory(tmp_path: Path) -> None:
    with pytest.raises(FaultCatalogLoadError, match="contains no YAML files"):
        load_fault_catalog(tmp_path)


def test_reports_invalid_definition_path(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("fault_id: invalid\nunknown: true\n", encoding="utf-8")

    with pytest.raises(FaultCatalogLoadError, match="invalid.yaml"):
        load_fault_catalog(tmp_path)


def test_rejects_duplicate_fault_ids(tmp_path: Path) -> None:
    content = """\
schema_version: "1.0"
fault_id: duplicate-fault
description: duplicate
category: status
matcher:
  method: GET
  path_regex: "^/items$"
mutation:
  type: replace_status
  status_code: 500
"""
    (tmp_path / "one.yaml").write_text(content, encoding="utf-8")
    (tmp_path / "two.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(FaultCatalogLoadError, match="duplicate fault ID"):
        load_fault_catalog(tmp_path)
