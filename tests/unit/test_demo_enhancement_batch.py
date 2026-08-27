from pathlib import Path

from openapi_ai_test_evaluator.domain.test_case import AssertionOperator, RelationType
from openapi_ai_test_evaluator.spec import load_openapi
from openapi_ai_test_evaluator.validation import (
    load_test_case_batch,
    validate_test_case_batch_semantics,
)

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")
BATCH = load_test_case_batch(
    ROOT / "benchmarks" / "demo_items" / "enhancements" / "shared-relations.yaml"
)


def test_demo_shared_enhancement_batch_is_semantically_valid() -> None:
    assert validate_test_case_batch_semantics(BATCH, SPEC) == []


def test_demo_shared_enhancement_batch_covers_every_v1_relation_and_uniqueness() -> None:
    relation_types = {
        relation.type for case in BATCH.cases for relation in case.relations
    }
    assertion_operators = {
        assertion.operator
        for case in BATCH.cases
        for step in [*case.setup, *case.steps, *case.cleanup]
        for assertion in step.assertions
    }

    assert len(BATCH.cases) == 7
    assert relation_types == set(RelationType)
    assert AssertionOperator.ITEMS_UNIQUE_BY in assertion_operators
