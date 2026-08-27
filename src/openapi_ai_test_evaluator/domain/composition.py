"""Strict provenance records for generator cases plus shared enhancements."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from openapi_ai_test_evaluator.domain.contracts import ContractModel, Identifier

PositiveInt = Annotated[int, Field(ge=1)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class BatchArtifactMetadata(ContractModel):
    """Stable identity and size of one canonical TestCaseBatch."""

    case_count: PositiveInt
    sha256: Sha256Digest


class EnhancementPackMetadata(ContractModel):
    """One named, generator-independent enhancement batch."""

    pack_id: Identifier
    batch: BatchArtifactMetadata


class SuiteCompositionRecord(ContractModel):
    """Receipt proving how one executable augmented suite was composed."""

    schema_version: Literal["1.0"]
    kind: Literal["SuiteCompositionRecord"]
    composition_id: Identifier
    base_batch: BatchArtifactMetadata
    enhancements: list[EnhancementPackMetadata] = Field(min_length=1)
    composed_batch: BatchArtifactMetadata

    @model_validator(mode="after")
    def validate_composition(self) -> Self:
        pack_ids = [enhancement.pack_id for enhancement in self.enhancements]
        if len(pack_ids) != len(set(pack_ids)):
            raise ValueError("enhancement pack IDs must be unique")
        expected_count = self.base_batch.case_count + sum(
            enhancement.batch.case_count for enhancement in self.enhancements
        )
        if self.composed_batch.case_count != expected_count:
            raise ValueError("composed case count must equal base plus enhancements")
        return self


__all__ = [
    "BatchArtifactMetadata",
    "EnhancementPackMetadata",
    "SuiteCompositionRecord",
]
