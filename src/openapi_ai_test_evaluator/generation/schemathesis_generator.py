"""Generate stateless Schemathesis cases without sending HTTP requests."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
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
    """Explicit per-phase budget for the primary stateless baseline."""

    example_case_limit: int = Field(default=5, ge=0, le=100)
    coverage_positive_case_limit: int = Field(default=5, ge=0, le=100)
    coverage_negative_case_limit: int = Field(default=5, ge=0, le=100)
    fuzzing_positive_case_count: int = Field(default=5, ge=0, le=100)
    fuzzing_negative_case_count: int = Field(default=5, ge=0, le=100)
    seed: int = 0

    @model_validator(mode="after")
    def validate_total_case_count(self) -> Self:
        total = (
            self.example_case_limit
            + self.coverage_positive_case_limit
            + self.coverage_negative_case_limit
            + self.fuzzing_positive_case_count
            + self.fuzzing_negative_case_count
        )
        if total < 1:
            raise ValueError("at least one Schemathesis case must be requested")
        if total > 100:
            raise ValueError("Schemathesis case count cannot exceed 100")
        return self


def generate_schemathesis_batch(
    schema: BaseSchema,
    spec: OpenAPISpec,
    config: SchemathesisGenerationConfig,
) -> SchemathesisBatchAdaptation:
    """Generate all bounded stateless phases while leaving HTTP I/O to the runner."""
    collector = SchemathesisBatchCollector(spec, seed=config.seed)
    operations = [result.ok() for result in schema.get_all_operations()]

    _collect_examples(
        operations,
        collector,
        limit=config.example_case_limit,
        random_seed=config.seed,
    )
    schema.reset_coverage_state()
    _collect_coverage(
        schema,
        operations,
        collector,
        mode=schemathesis.GenerationMode.POSITIVE,
        limit=config.coverage_positive_case_limit,
    )
    _collect_coverage(
        schema,
        operations,
        collector,
        mode=schemathesis.GenerationMode.NEGATIVE,
        limit=config.coverage_negative_case_limit,
    )
    _collect_fuzzing(
        schema,
        collector,
        mode=schemathesis.GenerationMode.POSITIVE,
        count=config.fuzzing_positive_case_count,
        random_seed=config.seed,
    )
    _collect_fuzzing(
        schema,
        collector,
        mode=schemathesis.GenerationMode.NEGATIVE,
        count=config.fuzzing_negative_case_count,
        random_seed=config.seed,
    )
    return collector.finish()


def _collect_examples(
    operations: list[APIOperation],
    collector: SchemathesisBatchCollector,
    *,
    limit: int,
    random_seed: int,
) -> None:
    if limit == 0:
        return

    strategies = (
        strategy
        for operation in operations
        for strategy in operation.get_strategies_from_examples()
    )
    for index, strategy in enumerate(strategies):
        if index >= limit:
            break
        _collect_one_strategy(strategy, collector, random_seed=random_seed + index)


def _collect_coverage(
    schema: BaseSchema,
    operations: list[APIOperation],
    collector: SchemathesisBatchCollector,
    *,
    mode: schemathesis.GenerationMode,
    limit: int,
) -> None:
    if limit == 0:
        return

    case_iterators: deque[Iterator[Case]] = deque(
        iter(
            schema.iter_coverage_cases(
                operation,
                generation_modes=[mode],
                generation_config=schema.config.generation,
            )
        )
        for operation in operations
    )
    collected = 0
    while case_iterators and collected < limit:
        case_iterator = case_iterators.popleft()
        try:
            case = next(case_iterator)
        except StopIteration:
            continue
        collector.add(case)
        collected += 1
        case_iterators.append(case_iterator)


def _collect_fuzzing(
    schema: BaseSchema,
    collector: SchemathesisBatchCollector,
    *,
    mode: schemathesis.GenerationMode,
    count: int,
    random_seed: int,
) -> None:
    if count == 0:
        return

    strategy = schema.as_strategy(generation_mode=mode)

    _collect_strategy(
        strategy,
        collector,
        count=count,
        random_seed=random_seed,
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
