"""Aggregate paired EvaluationResult artifacts without hiding raw suite sizes."""

from __future__ import annotations

from collections import Counter, defaultdict
from math import fsum

from openapi_ai_test_evaluator.domain.evaluation import (
    EvaluationResult,
    FaultEvaluationOutcome,
)
from openapi_ai_test_evaluator.domain.reporting import (
    ComparedGenerator,
    ComparisonMode,
    ComparisonResult,
    FaultStability,
    MetricStatistics,
    SuiteComparison,
)


class ComparisonInputError(ValueError):
    """Evaluation artifacts cannot form one paired comparison."""


def compare_evaluations(
    evaluations: list[EvaluationResult],
    *,
    comparison_id: str,
) -> ComparisonResult:
    """Aggregate two or more suites across the same repetitions and fault set."""
    if not evaluations:
        raise ComparisonInputError("at least two evaluated suites are required")
    evaluation_ids = [evaluation.evaluation_id for evaluation in evaluations]
    if len(evaluation_ids) != len(set(evaluation_ids)):
        raise ComparisonInputError("evaluation IDs must be unique")
    spec_ids = {evaluation.spec_id for evaluation in evaluations}
    if len(spec_ids) != 1:
        raise ComparisonInputError("all evaluations must use the same OpenAPI spec")

    grouped: dict[str, list[EvaluationResult]] = defaultdict(list)
    for evaluation in evaluations:
        grouped[evaluation.suite_id].append(evaluation)
    if len(grouped) < 2:
        raise ComparisonInputError("at least two distinct suite IDs are required")

    ordered_groups = {
        suite_id: sorted(group, key=lambda evaluation: evaluation.repetition)
        for suite_id, group in sorted(grouped.items())
    }
    first_group = next(iter(ordered_groups.values()))
    repetitions = [evaluation.repetition for evaluation in first_group]
    fault_ids = sorted(fault.fault_id for fault in first_group[0].faults)

    suites: list[SuiteComparison] = []
    for suite_id, group in ordered_groups.items():
        actual_repetitions = [evaluation.repetition for evaluation in group]
        if len(actual_repetitions) != len(set(actual_repetitions)):
            raise ComparisonInputError(f"suite {suite_id!r} has duplicate repetitions")
        if actual_repetitions != repetitions:
            raise ComparisonInputError("all suites must contain the same paired repetitions")
        if any(
            sorted(fault.fault_id for fault in evaluation.faults) != fault_ids
            for evaluation in group
        ):
            raise ComparisonInputError("all evaluations must use the same fault set")
        _validate_generator_identity(group)
        suites.append(_aggregate_suite(group, fault_ids))

    return ComparisonResult(
        schema_version="1.0",
        kind="ComparisonResult",
        comparison_id=comparison_id,
        mode=ComparisonMode.NATIVE_SUITE,
        spec_id=first_group[0].spec_id,
        repetitions=repetitions,
        fault_ids=fault_ids,
        suites=suites,
    )


def _validate_generator_identity(group: list[EvaluationResult]) -> None:
    first = group[0].generator
    identity = (first.kind, first.name, first.version, first.model)
    if any(
        (generator.kind, generator.name, generator.version, generator.model) != identity
        for generator in (evaluation.generator for evaluation in group[1:])
    ):
        raise ComparisonInputError(
            f"suite {group[0].suite_id!r} changes generator identity across repetitions"
        )


def _aggregate_suite(
    evaluations: list[EvaluationResult],
    fault_ids: list[str],
) -> SuiteComparison:
    first = evaluations[0]
    return SuiteComparison(
        suite_id=first.suite_id,
        generator=ComparedGenerator(
            kind=first.generator.kind,
            name=first.generator.name,
            version=first.generator.version,
            model=first.generator.model,
        ),
        repetitions=[evaluation.repetition for evaluation in evaluations],
        evaluation_ids=[evaluation.evaluation_id for evaluation in evaluations],
        received_case_count=_statistics(
            [evaluation.admission.received_case_count for evaluation in evaluations]
        ),
        admitted_case_count=_statistics(
            [evaluation.admission.admitted_case_count for evaluation in evaluations]
        ),
        admission_rate=_statistics(
            [evaluation.admission.admission_rate for evaluation in evaluations]
        ),
        executable_case_rate=_statistics(
            [evaluation.execution.executable_case_rate for evaluation in evaluations]
        ),
        clean_false_positive_rate=_statistics(
            [evaluation.execution.clean_false_positive_rate for evaluation in evaluations]
        ),
        operation_coverage_rate=_statistics(
            [evaluation.execution.operation_coverage_rate for evaluation in evaluations]
        ),
        fault_detection_rate=_statistics(
            [evaluation.fault_summary.fault_detection_rate for evaluation in evaluations]
        ),
        faults_detected_per_100_requests=_statistics(
            [
                evaluation.fault_summary.faults_detected_per_100_requests
                for evaluation in evaluations
            ]
        ),
        clean_request_count=_statistics(
            [evaluation.execution.clean_request_count for evaluation in evaluations]
        ),
        fault_request_count=_statistics(
            [evaluation.execution.fault_request_count for evaluation in evaluations]
        ),
        total_request_count=_statistics(
            [evaluation.execution.total_request_count for evaluation in evaluations]
        ),
        generation_request_count=_statistics(
            [evaluation.generator.generation_request_count for evaluation in evaluations]
        ),
        generation_duration_ms=_statistics(
            [evaluation.generator.generation_duration_ms for evaluation in evaluations]
        ),
        execution_duration_ms=_statistics(
            [evaluation.execution.execution_duration_ms for evaluation in evaluations]
        ),
        input_tokens=_statistics(
            [evaluation.generator.token_usage.input_tokens for evaluation in evaluations]
        ),
        output_tokens=_statistics(
            [evaluation.generator.token_usage.output_tokens for evaluation in evaluations]
        ),
        total_tokens=_statistics(
            [evaluation.generator.token_usage.total_tokens for evaluation in evaluations]
        ),
        cached_input_tokens=_statistics(
            [evaluation.generator.token_usage.cached_input_tokens for evaluation in evaluations]
        ),
        reasoning_tokens=_statistics(
            [evaluation.generator.token_usage.reasoning_tokens for evaluation in evaluations]
        ),
        estimated_cost_usd=_statistics(
            [evaluation.generator.estimated_cost_usd for evaluation in evaluations]
        ),
        faults=[_aggregate_fault(evaluations, fault_id) for fault_id in fault_ids],
    )


def _aggregate_fault(
    evaluations: list[EvaluationResult],
    fault_id: str,
) -> FaultStability:
    fault_results = [
        next(fault for fault in evaluation.faults if fault.fault_id == fault_id)
        for evaluation in evaluations
    ]
    outcomes = Counter(fault.outcome for fault in fault_results)
    detected = outcomes[FaultEvaluationOutcome.DETECTED]
    not_detected = outcomes[FaultEvaluationOutcome.NOT_DETECTED]
    evaluable = detected + not_detected
    return FaultStability(
        fault_id=fault_id,
        repetition_count=len(evaluations),
        detected_count=detected,
        not_detected_count=not_detected,
        not_triggered_count=outcomes[FaultEvaluationOutcome.NOT_TRIGGERED],
        no_eligible_case_count=outcomes[FaultEvaluationOutcome.NO_ELIGIBLE_CASE],
        inconclusive_count=outcomes[FaultEvaluationOutcome.INCONCLUSIVE],
        evaluable_count=evaluable,
        detection_rate=detected / evaluable if evaluable else None,
        first_detection_request=_statistics(
            [fault.first_detection_request for fault in fault_results]
        ),
    )


def _statistics(values: list[int | float | None]) -> MetricStatistics:
    present = [float(value) for value in values if value is not None]
    if not present:
        return MetricStatistics(
            values=[],
            sample_count=0,
            missing_count=len(values),
            mean=None,
            stddev=None,
            minimum=None,
            maximum=None,
        )
    mean = fsum(present) / len(present)
    variance = fsum((value - mean) ** 2 for value in present) / len(present)
    return MetricStatistics(
        values=present,
        sample_count=len(present),
        missing_count=len(values) - len(present),
        mean=mean,
        stddev=variance**0.5,
        minimum=min(present),
        maximum=max(present),
    )


__all__ = ["ComparisonInputError", "compare_evaluations"]
