"""Load strict fault definitions from a benchmark fault directory."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from openapi_ai_test_evaluator.domain.fault import FaultDefinition


class FaultCatalogLoadError(ValueError):
    """A fault catalog directory does not contain a usable strict catalog."""


def load_fault_catalog(directory: Path) -> list[FaultDefinition]:
    """Load sorted YAML fault definitions and reject duplicate fault IDs."""
    if not directory.is_dir():
        raise FaultCatalogLoadError(f"fault catalog directory does not exist: {directory}")
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise FaultCatalogLoadError(f"fault catalog contains no YAML files: {directory}")

    definitions: list[FaultDefinition] = []
    known_ids: dict[str, Path] = {}
    for path in paths:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            definition = FaultDefinition.model_validate(document)
        except (OSError, yaml.YAMLError, ValidationError) as error:
            raise FaultCatalogLoadError(f"invalid fault definition {path}: {error}") from error
        if previous_path := known_ids.get(definition.fault_id):
            raise FaultCatalogLoadError(
                f"duplicate fault ID {definition.fault_id!r} in {previous_path} and {path}"
            )
        known_ids[definition.fault_id] = path
        definitions.append(definition)
    return definitions


__all__ = ["FaultCatalogLoadError", "load_fault_catalog"]
