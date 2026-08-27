"""Pure composition of native generator cases and shared enhancement packs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

from openapi_ai_test_evaluator.domain.composition import (
    BatchArtifactMetadata,
    EnhancementPackMetadata,
    SuiteCompositionRecord,
)
from openapi_ai_test_evaluator.domain.test_case import TestCaseBatch


class SuiteCompositionError(ValueError):
    """Input batches cannot form one unambiguous executable suite."""


@dataclass(frozen=True, slots=True)
class NamedEnhancementBatch:
    """A loaded enhancement batch paired with its stable experiment ID."""

    pack_id: str
    batch: TestCaseBatch


@dataclass(frozen=True, slots=True)
class ComposedTestCaseBatch:
    """The merged executable batch and its provenance receipt."""

    batch: TestCaseBatch
    record: SuiteCompositionRecord


def compose_test_case_batches(
    base_batch: TestCaseBatch,
    enhancements: Sequence[NamedEnhancementBatch],
    *,
    composition_id: str,
) -> ComposedTestCaseBatch:
    """Append named enhancement batches without mutating any source batch."""
    if not enhancements:
        raise SuiteCompositionError("at least one enhancement batch is required")

    pack_ids = [enhancement.pack_id for enhancement in enhancements]
    if len(pack_ids) != len(set(pack_ids)):
        raise SuiteCompositionError("enhancement pack IDs must be unique")

    cases = list(base_batch.cases)
    origins = {case.id: "base batch" for case in base_batch.cases}
    enhancement_metadata: list[EnhancementPackMetadata] = []
    for enhancement in enhancements:
        for case in enhancement.batch.cases:
            existing_origin = origins.get(case.id)
            if existing_origin is not None:
                raise SuiteCompositionError(
                    f"case ID {case.id!r} appears in both {existing_origin} "
                    f"and enhancement {enhancement.pack_id!r}"
                )
            origins[case.id] = f"enhancement {enhancement.pack_id!r}"
            cases.append(case)
        enhancement_metadata.append(
            EnhancementPackMetadata(
                pack_id=enhancement.pack_id,
                batch=_batch_metadata(enhancement.batch),
            )
        )

    composed_batch = TestCaseBatch(schema_version="1.0", cases=cases)
    record = SuiteCompositionRecord(
        schema_version="1.0",
        kind="SuiteCompositionRecord",
        composition_id=composition_id,
        base_batch=_batch_metadata(base_batch),
        enhancements=enhancement_metadata,
        composed_batch=_batch_metadata(composed_batch),
    )
    return ComposedTestCaseBatch(batch=composed_batch, record=record)


def case_batch_sha256(batch: TestCaseBatch) -> str:
    """Hash canonical JSON so YAML/JSON formatting cannot change batch identity."""
    canonical = json.dumps(
        batch.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _batch_metadata(batch: TestCaseBatch) -> BatchArtifactMetadata:
    return BatchArtifactMetadata(
        case_count=len(batch.cases),
        sha256=case_batch_sha256(batch),
    )


__all__ = [
    "ComposedTestCaseBatch",
    "NamedEnhancementBatch",
    "SuiteCompositionError",
    "case_batch_sha256",
    "compose_test_case_batches",
]
