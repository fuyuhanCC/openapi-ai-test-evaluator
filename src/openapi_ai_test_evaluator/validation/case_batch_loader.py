"""JSON and YAML loading for runner-ready TestCaseBatch contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import ValidationError

from openapi_ai_test_evaluator.domain import TestCaseBatch

BatchDocumentFormat = Literal["json", "yaml"]


class TestCaseBatchLoadError(ValueError):
    """A TestCaseBatch could not be decoded or structurally validated."""


def parse_test_case_batch(
    text: str,
    *,
    document_format: BatchDocumentFormat = "json",
    source: str = "<generated-output>",
) -> TestCaseBatch:
    """Decode one JSON or YAML document and validate its TestCaseBatch structure."""
    if document_format == "json":
        try:
            raw_batch = json.loads(text)
        except json.JSONDecodeError as error:
            raise TestCaseBatchLoadError(f"invalid JSON in {source}: {error}") from error
    elif document_format == "yaml":
        try:
            raw_batch = yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise TestCaseBatchLoadError(f"invalid YAML in {source}: {error}") from error
    else:
        raise TestCaseBatchLoadError(
            f"unsupported TestCaseBatch format {document_format!r}; use 'json' or 'yaml'"
        )

    if not isinstance(raw_batch, dict):
        raise TestCaseBatchLoadError(f"{source} must contain a mapping at the top level")

    try:
        return TestCaseBatch.model_validate(raw_batch)
    except ValidationError as error:
        raise TestCaseBatchLoadError(f"invalid TestCaseBatch in {source}: {error}") from error


def load_test_case_batch(path: Path) -> TestCaseBatch:
    """Read and validate a TestCaseBatch file, inferring JSON or YAML by suffix."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        document_format: BatchDocumentFormat = "json"
    elif suffix in {".yaml", ".yml"}:
        document_format = "yaml"
    else:
        raise TestCaseBatchLoadError(
            f"unsupported TestCaseBatch file suffix {path.suffix!r}; use .json, .yaml, or .yml"
        )

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise TestCaseBatchLoadError(f"cannot read {path}: {error}") from error

    return parse_test_case_batch(
        text,
        document_format=document_format,
        source=str(path),
    )
