"""Derive one strict EvaluationResult from raw clean and fault runs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from openapi_ai_test_evaluator.domain.evaluation import (
    CaseAdmissionMetrics,
    EvaluationResult,
    FaultEvaluation,
    FaultEvaluationOutcome,
    FaultSummaryMetrics,
    GeneratorKind,
    GeneratorMetadata,
    SuiteExecutionMetrics,
)
from openapi_ai_test_evaluator.domain.execution import (
    ExecutionOutcome,
    FaultTriggerStatus,
    RunResult,
)
from openapi_ai_test_evaluator.domain.fault import FAULT_ID_RESPONSE_HEADER
from openapi_ai_test_evaluator.domain.generation import (
    AdaptationRecord,
    GenerationRecord,
    GenerationStatus,
    GenerationTokenUsage,
)
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.evaluation.suite_runner import FaultRun, SuiteExecution


class EvaluationInputError(ValueError):
    """Raw suite inputs cannot support a trustworthy metric calculation."""


def evaluate_suite_execution(
    execution: SuiteExecution,
    spec: OpenAPISpec,
    source_record: GenerationRecord | AdaptationRecord,
    *,
    evaluation_id: str,
) -> EvaluationResult:
    """Evaluate one generator suite without modifying or hiding its raw runs."""
    generator, admission = _source_metrics(source_record)
    _validate_execution(execution, spec, admission.admitted_case_count)

    clean_passed_ids = {
        case.case_id for case in execution.clean.cases if case.outcome is ExecutionOutcome.PASSED
    }
    fault_evaluations = [
        _evaluate_fault(fault_run, clean_passed_ids) for fault_run in execution.faults
    ]

    clean_outcomes = Counter(case.outcome for case in execution.clean.cases)
    clean_request_count = _request_count(execution.clean)
    fault_request_count = sum(_request_count(fault.result) for fault in execution.faults)
    eligible_operation_ids = {
        operation_id
        for operation_id, operation in spec.operations.items()
        if not operation.unsupported_reasons
    }
    covered_operation_ids = {
        step.operation_id
        for case in execution.clean.cases
        for step in case.steps
        if step.request is not None and step.operation_id in eligible_operation_ids
    }
    deterministic_count = (
        clean_outcomes[ExecutionOutcome.PASSED] + clean_outcomes[ExecutionOutcome.FAILED]
    )
    admitted_count = admission.admitted_case_count
    suite_metrics = SuiteExecutionMetrics(
        admitted_case_count=admitted_count,
        clean_passed_case_count=clean_outcomes[ExecutionOutcome.PASSED],
        clean_failed_case_count=clean_outcomes[ExecutionOutcome.FAILED],
        clean_error_case_count=clean_outcomes[ExecutionOutcome.ERROR],
        clean_skipped_case_count=clean_outcomes[ExecutionOutcome.SKIPPED],
        executable_case_rate=deterministic_count / admitted_count,
        clean_false_positive_rate=(
            clean_outcomes[ExecutionOutcome.FAILED] / deterministic_count
            if deterministic_count
            else None
        ),
        eligible_operation_count=len(eligible_operation_ids),
        covered_operation_count=len(covered_operation_ids),
        operation_coverage_rate=(
            len(covered_operation_ids) / len(eligible_operation_ids)
            if eligible_operation_ids
            else None
        ),
        clean_request_count=clean_request_count,
        fault_request_count=fault_request_count,
        total_request_count=clean_request_count + fault_request_count,
        execution_duration_ms=(
            execution.clean.duration_ms
            + sum(fault.result.duration_ms for fault in execution.faults)
        ),
    )
    fault_summary = _fault_summary(fault_evaluations, fault_request_count)
    return EvaluationResult(
        schema_version="1.0",
        kind="EvaluationResult",
        evaluation_id=evaluation_id,
        suite_id=execution.suite_id,
        repetition=execution.repetition,
        spec_id=spec.spec_id,
        generator=generator,
        admission=admission,
        execution=suite_metrics,
        fault_summary=fault_summary,
        clean_run_id=execution.clean.run_id,
        faults=fault_evaluations,
    )


def validate_source_record_case_count(
    source_record: GenerationRecord | AdaptationRecord,
    case_count: int,
) -> None:
    """Reject a source record that does not describe the frozen executable batch."""
    _, admission = _source_metrics(source_record)
    if admission.admitted_case_count != case_count:
        raise EvaluationInputError(
            "source record admitted case count does not match the frozen batch"
        )


def _source_metrics(
    record: GenerationRecord | AdaptationRecord,
) -> tuple[GeneratorMetadata, CaseAdmissionMetrics]:
    if isinstance(record, GenerationRecord):
        if record.status is not GenerationStatus.SUCCEEDED or record.case_admission is None:
            raise EvaluationInputError(
                "LLM evaluation requires a successful record with case admission metrics"
            )
        summary = record.case_admission
        generator = GeneratorMetadata(
            kind=GeneratorKind.LLM,
            name=record.provider,
            version=None,
            model=record.model,
            source_record_id=record.generation_id,
            generation_request_count=record.request_count,
            generation_duration_ms=record.duration_ms,
            token_usage=record.token_usage,
            estimated_cost_usd=record.estimated_cost_usd,
        )
        admitted = summary.admitted_case_count
        received = summary.received_case_count
        rejected = summary.rejected_case_count
    else:
        generator = GeneratorMetadata(
            kind=GeneratorKind.SCHEMA_TOOL,
            name=record.tool,
            version=record.tool_version,
            model=None,
            source_record_id=None,
            generation_request_count=0,
            generation_duration_ms=record.duration_ms,
            token_usage=GenerationTokenUsage(),
            estimated_cost_usd=None,
        )
        admitted = record.adapted_case_count
        received = record.received_case_count
        rejected = record.rejected_case_count

    if admitted == 0 or received == 0:
        raise EvaluationInputError("evaluation requires at least one admitted source case")
    return generator, CaseAdmissionMetrics(
        received_case_count=received,
        admitted_case_count=admitted,
        rejected_case_count=rejected,
        admission_rate=admitted / received,
    )


def _validate_execution(
    execution: SuiteExecution,
    spec: OpenAPISpec,
    admitted_case_count: int,
) -> None:
    clean = execution.clean
    if clean.spec_id != spec.spec_id:
        raise EvaluationInputError("clean run spec_id does not match the evaluated OpenAPI")
    if clean.batch_name != execution.suite_id:
        raise EvaluationInputError("clean run batch_name does not match suite_id")
    if clean.fault.trigger_status is not FaultTriggerStatus.NOT_CONFIGURED:
        raise EvaluationInputError("clean run must not have a configured fault")
    if len(clean.cases) != admitted_case_count:
        raise EvaluationInputError("admitted case count does not match clean run cases")

    expected_case_ids = [case.case_id for case in clean.cases]
    known_fault_ids: set[str] = set()
    for fault_run in execution.faults:
        if fault_run.fault_id in known_fault_ids:
            raise EvaluationInputError("fault IDs must be unique within suite execution")
        known_fault_ids.add(fault_run.fault_id)
        result = fault_run.result
        if result.spec_id != spec.spec_id or result.batch_name != execution.suite_id:
            raise EvaluationInputError("fault run identity does not match the clean run")
        if [case.case_id for case in result.cases] != expected_case_ids:
            raise EvaluationInputError("fault runs must preserve clean case IDs and order")
        if result.fault.configured_fault_id != fault_run.fault_id:
            raise EvaluationInputError("fault run observation does not match fault ID")
        if result.fault.trigger_status not in {
            FaultTriggerStatus.TRIGGERED,
            FaultTriggerStatus.NOT_TRIGGERED,
        }:
            raise EvaluationInputError("fault run requires a final observed trigger status")


def _evaluate_fault(
    fault_run: FaultRun,
    clean_passed_ids: set[str],
) -> FaultEvaluation:
    result = fault_run.result
    marked_steps = _fault_marked_steps(result, fault_run.fault_id)
    observed_trigger_count = len(marked_steps)
    if observed_trigger_count != result.fault.trigger_count:
        raise EvaluationInputError(
            f"fault {fault_run.fault_id!r} trigger count does not match response evidence"
        )

    triggered_case_ids = _ordered_unique(case_id for case_id, _ in marked_steps)
    eligible_case_ids = [case_id for case_id in triggered_case_ids if case_id in clean_passed_ids]
    fault_cases = {case.case_id: case for case in result.cases}
    detected_case_ids = [
        case_id
        for case_id in eligible_case_ids
        if fault_cases[case_id].outcome is ExecutionOutcome.FAILED
    ]
    errored_case_ids = [
        case_id
        for case_id in eligible_case_ids
        if fault_cases[case_id].outcome is ExecutionOutcome.ERROR
    ]

    if result.fault.trigger_status is FaultTriggerStatus.NOT_TRIGGERED:
        outcome = FaultEvaluationOutcome.NOT_TRIGGERED
    elif detected_case_ids:
        outcome = FaultEvaluationOutcome.DETECTED
    elif not eligible_case_ids:
        outcome = FaultEvaluationOutcome.NO_ELIGIBLE_CASE
    elif any(
        fault_cases[case_id].outcome is ExecutionOutcome.PASSED for case_id in eligible_case_ids
    ):
        outcome = FaultEvaluationOutcome.NOT_DETECTED
    else:
        outcome = FaultEvaluationOutcome.INCONCLUSIVE

    first_detection_request = None
    if detected_case_ids:
        detected_set = set(detected_case_ids)
        first_detection_request = min(
            ordinal for case_id, ordinal in marked_steps if case_id in detected_set
        )
    return FaultEvaluation(
        fault_id=fault_run.fault_id,
        run_id=result.run_id,
        outcome=outcome,
        trigger_count=result.fault.trigger_count,
        request_count=_request_count(result),
        triggered_case_ids=triggered_case_ids,
        eligible_triggered_case_ids=eligible_case_ids,
        detected_case_ids=detected_case_ids,
        errored_case_ids=errored_case_ids,
        first_detection_request=first_detection_request,
    )


def _fault_marked_steps(
    result: RunResult,
    fault_id: str,
) -> list[tuple[str, int]]:
    marked: list[tuple[str, int]] = []
    request_ordinal = 0
    for case in result.cases:
        for step in case.steps:
            if step.request is not None:
                request_ordinal += 1
            if (
                step.response is not None
                and step.response.headers.get(FAULT_ID_RESPONSE_HEADER) == fault_id
            ):
                marked.append((case.case_id, request_ordinal))
    return marked


def _request_count(result: RunResult) -> int:
    return sum(step.request is not None for case in result.cases for step in case.steps)


def _ordered_unique(values: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return ordered


def _fault_summary(
    faults: list[FaultEvaluation],
    fault_request_count: int,
) -> FaultSummaryMetrics:
    outcomes = Counter(fault.outcome for fault in faults)
    detected = outcomes[FaultEvaluationOutcome.DETECTED]
    not_detected = outcomes[FaultEvaluationOutcome.NOT_DETECTED]
    evaluable = detected + not_detected
    return FaultSummaryMetrics(
        configured_fault_count=len(faults),
        triggered_fault_count=(len(faults) - outcomes[FaultEvaluationOutcome.NOT_TRIGGERED]),
        evaluable_fault_count=evaluable,
        detected_fault_count=detected,
        not_detected_fault_count=not_detected,
        not_triggered_fault_count=outcomes[FaultEvaluationOutcome.NOT_TRIGGERED],
        no_eligible_case_fault_count=outcomes[FaultEvaluationOutcome.NO_ELIGIBLE_CASE],
        inconclusive_fault_count=outcomes[FaultEvaluationOutcome.INCONCLUSIVE],
        fault_detection_rate=detected / evaluable if evaluable else None,
        faults_detected_per_100_requests=(
            detected * 100 / fault_request_count if fault_request_count else 0
        ),
    )


__all__ = [
    "EvaluationInputError",
    "evaluate_suite_execution",
    "validate_source_record_case_count",
]
