"""Generate stateless Schemathesis cases without sending HTTP requests."""

from __future__ import annotations

from typing import Self

import schemathesis
from hypothesis import Phase, given, seed, settings
from hypothesis.strategies import SearchStrategy
from pydantic import Field, model_validator
from schemathesis import APIOperation, BaseSchema, Case

from openapi_ai_test_evaluator.domain.contracts import ContractModel
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.generation.schemathesis_batch import (
    SchemathesisBatchAdaptation,
    SchemathesisBatchCollector,
)


class SchemathesisGenerationConfig(ContractModel):
    """Complete finite phases plus bounded per-operation fuzzing."""

    include_examples: bool = True
    include_coverage: bool = True
    fuzzing_positive_cases_per_operation: int = Field(default=5, ge=0, le=100)
    fuzzing_negative_cases_per_operation: int = Field(default=5, ge=0, le=100)
    seed: int = 0

    @model_validator(mode="after")
    def require_a_generation_source(self) -> Self:
        if (
            not self.include_examples
            and not self.include_coverage
            and self.fuzzing_positive_cases_per_operation == 0
            and self.fuzzing_negative_cases_per_operation == 0
        ):
            raise ValueError("at least one Schemathesis generation source must be enabled")
        return self


def generate_schemathesis_batch(
    schema: BaseSchema,
    spec: OpenAPISpec,
    config: SchemathesisGenerationConfig,
) -> SchemathesisBatchAdaptation:
    """Generate complete finite phases and bounded fuzzing without HTTP I/O."""
    collector = SchemathesisBatchCollector(spec, seed=config.seed)
    operations = [result.ok() for result in schema.get_all_operations()]

    if config.include_examples:
        _collect_all_examples(operations, collector, random_seed=config.seed)
    if config.include_coverage:
        schema.reset_coverage_state()
        _collect_all_coverage(schema, operations, collector)
    _collect_fuzzing(
        operations,
        collector,
        mode=schemathesis.GenerationMode.POSITIVE,
        cases_per_operation=config.fuzzing_positive_cases_per_operation,
        random_seed=config.seed,
    )
    _collect_fuzzing(
        operations,
        collector,
        mode=schemathesis.GenerationMode.NEGATIVE,
        cases_per_operation=config.fuzzing_negative_cases_per_operation,
        random_seed=config.seed + len(operations),
    )
    return collector.finish()


def _collect_all_examples(
    operations: list[APIOperation],
    collector: SchemathesisBatchCollector,
    *,
    random_seed: int,
) -> None:
    strategies = (
        strategy
        for operation in operations
        for strategy in operation.get_strategies_from_examples()
    )
    for index, strategy in enumerate(strategies):
        _collect_one_strategy(strategy, collector, random_seed=random_seed + index)


def _collect_all_coverage(
    schema: BaseSchema,
    operations: list[APIOperation],
    collector: SchemathesisBatchCollector,
) -> None:
    for operation in operations:
        for case in schema.iter_coverage_cases(
            operation,
            generation_modes=[
                schemathesis.GenerationMode.POSITIVE,
                schemathesis.GenerationMode.NEGATIVE,
            ],
            generation_config=schema.config.generation,
        ):
            collector.add(case)


def _collect_fuzzing(
    operations: list[APIOperation],
    collector: SchemathesisBatchCollector,
    *,
    mode: schemathesis.GenerationMode,
    cases_per_operation: int,
    random_seed: int,
) -> None:
    if cases_per_operation == 0:
        return

    for index, operation in enumerate(operations):
        _collect_strategy(
            operation.as_strategy(generation_mode=mode),
            collector,
            count=cases_per_operation,
            random_seed=random_seed + index,
        )


def _collect_one_strategy(
    strategy: SearchStrategy[Case],
    collector: SchemathesisBatchCollector,
    *,
    random_seed: int,
) -> None:
    _collect_strategy(strategy, collector, count=1, random_seed=random_seed)


def _collect_strategy(
    strategy: SearchStrategy[Case],
    collector: SchemathesisBatchCollector,
    *,
    count: int,
    random_seed: int,
) -> None:

    @seed(random_seed)
    @settings(
        max_examples=count,
        phases=(Phase.generate,),
        database=None,
        deadline=None,
    )
    @given(case=strategy)
    def collect(case: Case) -> None:
        collector.add(case)

    collect()
