from copy import deepcopy

import pytest

from openapi_ai_test_evaluator.domain.test_case import TestCaseBatch as CaseBatch
from openapi_ai_test_evaluator.generation import (
    NamedEnhancementBatch,
    SuiteCompositionError,
    case_batch_sha256,
    compose_test_case_batches,
)


def batch(*case_ids: str) -> CaseBatch:
    return CaseBatch.model_validate(
        {
            "schema_version": "1.0",
            "cases": [
                {
                    "id": case_id,
                    "steps": [{"id": "request", "operation_id": "listItems"}],
                }
                for case_id in case_ids
            ],
        }
    )


def test_composes_batches_and_records_stable_counts_and_hashes() -> None:
    base = batch("native-one", "native-two")
    enhancement = batch("relation-one")

    result = compose_test_case_batches(
        base,
        [NamedEnhancementBatch("shared-relations", enhancement)],
        composition_id="deepseek-augmented-r1",
    )

    assert [case.id for case in result.batch.cases] == [
        "native-one",
        "native-two",
        "shared-relations-relation-one",
    ]
    assert result.record.base_batch.case_count == 2
    assert result.record.enhancements[0].batch.case_count == 1
    assert result.record.composed_batch.case_count == 3
    assert result.record.base_batch.sha256 == case_batch_sha256(base)
    assert result.record.composed_batch.sha256 == case_batch_sha256(result.batch)


def test_hash_is_stable_for_equivalent_models_and_changes_with_content() -> None:
    original = batch("native-one")
    equivalent = deepcopy(original)
    changed = batch("native-two")

    assert case_batch_sha256(equivalent) == case_batch_sha256(original)
    assert case_batch_sha256(changed) != case_batch_sha256(original)


def test_namespaces_duplicate_source_case_ids_across_batches() -> None:
    result = compose_test_case_batches(
        batch("same-case"),
        [NamedEnhancementBatch("shared-relations", batch("same-case"))],
        composition_id="valid-composition",
    )

    assert [case.id for case in result.batch.cases] == [
        "same-case",
        "shared-relations-same-case",
    ]


def test_rejects_collision_with_namespaced_enhancement_case_id() -> None:
    with pytest.raises(SuiteCompositionError, match="namespaced case ID"):
        compose_test_case_batches(
            batch("shared-relations-same-case"),
            [NamedEnhancementBatch("shared-relations", batch("same-case"))],
            composition_id="invalid-composition",
        )


def test_namespaces_same_case_id_from_different_enhancement_packs() -> None:
    result = compose_test_case_batches(
        batch("native"),
        [
            NamedEnhancementBatch("shared-one", batch("same-case")),
            NamedEnhancementBatch("shared-two", batch("same-case")),
        ],
        composition_id="valid-composition",
    )

    assert [case.id for case in result.batch.cases] == [
        "native",
        "shared-one-same-case",
        "shared-two-same-case",
    ]


def test_rejects_duplicate_enhancement_pack_ids() -> None:
    with pytest.raises(SuiteCompositionError, match="pack IDs must be unique"):
        compose_test_case_batches(
            batch("native"),
            [
                NamedEnhancementBatch("shared", batch("first")),
                NamedEnhancementBatch("shared", batch("second")),
            ],
            composition_id="invalid-composition",
        )


def test_rejects_composition_without_an_enhancement() -> None:
    with pytest.raises(SuiteCompositionError, match="at least one enhancement"):
        compose_test_case_batches(
            batch("native"),
            [],
            composition_id="invalid-composition",
        )
