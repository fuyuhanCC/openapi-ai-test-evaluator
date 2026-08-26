"""Load strict source records required by evaluated suite runs."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, TypeAdapter, ValidationError

from openapi_ai_test_evaluator.domain.generation import AdaptationRecord, GenerationRecord

SourceRecord = Annotated[
    GenerationRecord | AdaptationRecord,
    Field(discriminator="kind"),
]
_SOURCE_RECORD_ADAPTER = TypeAdapter(SourceRecord)


class SourceRecordLoadError(ValueError):
    """A generation or adaptation record cannot be read or validated."""


def load_source_record(path: Path) -> GenerationRecord | AdaptationRecord:
    """Read one canonical GenerationRecord or AdaptationRecord JSON artifact."""
    try:
        serialized = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SourceRecordLoadError(f"cannot read {path}: {error}") from error
    try:
        return _SOURCE_RECORD_ADAPTER.validate_json(serialized)
    except ValidationError as error:
        raise SourceRecordLoadError(f"invalid source record {path}: {error}") from error


__all__ = ["SourceRecord", "SourceRecordLoadError", "load_source_record"]
