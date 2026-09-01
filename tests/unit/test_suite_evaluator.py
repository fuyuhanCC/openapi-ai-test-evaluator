import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openapi_ai_test_evaluator.domain.composition import SuiteCompositionRecord
from openapi_ai_test_evaluator.domain.evaluation import (
    FaultEvaluationOutcome,
    GeneratorKind,
)
from openapi_ai_test_evaluator.domain.execution import (
    BodySnapshot,
    ExecutionOutcome,
    FaultObservation,
    FaultTriggerStatus,
    HttpMethod,
    OutcomePolicy,
    RequestSnapshot,
    ResponseSnapshot,
    RunResult,
    StepPhase,
    StepResult,
)
from openapi_ai_test_evaluator.domain.execution import TestCaseResult as CaseResult
from openapi_ai_test_evaluator.domain.fault import FAULT_ID_RESPONSE_HEADER
from openapi_ai_test_evaluator.domain.generation import (
    AdaptationRecord,
    AdaptationSkipReason,
    CaseAdmissionRejection,
    CaseAdmissionStage,
    CaseAdmissionSummary,
    GenerationRecord,
    GenerationStatus,
    GenerationTokenUsage,
)
from openapi_ai_test_evaluator.evaluation import (
    EvaluatedSuite,
    EvaluationInputError,
    FaultRun,
    SuiteArtifactError,
    SuiteExecution,
    evaluate_suite_execution,
    write_evaluated_suite_artifacts,
)
from openapi_ai_test_evaluator.spec import load_openapi

SPEC = load_openapi(Path("examples/demo-items/openapi.yaml"))
STARTED_AT = datetime(2026, 8, 26, 12, tzinfo=UTC)


def empty_body() -> BodySnapshot:
    return BodySnapshot(media_type=None, value=None, size_bytes=0, truncated=False)


def step(
    operation_id: str,
    outcome: ExecutionOutcome,
    *,
    fault_id: str | None = None,
) -> StepResult:
    headers = {"content-type": "application/json"}
    if fault_id is not None:
        headers[FAULT_ID_RESPONSE_HEADER] = fault_id
    method = HttpMethod.POST if operation_id == "createItem" else HttpMethod.GET
    return StepResult(
        phase=StepPhase.MAIN,
        step_id=f"step-{operation_id.casefold()}",
        operation_id=operation_id,
        outcome_policy=OutcomePolicy.REQUIRED,
        outcome=outcome,
        duration_ms=1,
        retry_count=0,
        request=RequestSnapshot(
            method=method,
            path="/items",
            query=[],
            headers={},
            body=empty_body(),
        ),
        response=ResponseSnapshot(
            status_code=200,
            headers=headers,
            body=BodySnapshot(
                media_type="application/json",
                value={"items": [], "offset": 0, "limit": 20, "total": 0},
                size_bytes=46,
                truncated=False,
            ),
        ),
        extractions=[],
        assertions=[],
        errors=[],
    )


def case(
    case_id: str,
    operation_id: str,
    outcome: ExecutionOutcome,
    *,
    fault_id: str | None = None,
) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        outcome=outcome,
        steps=[step(operation_id, outcome, fault_id=fault_id)],
        relations=[],
        errors=[],
    )


def run(
    run_id: str,
    cases: list[CaseResult],
    *,
    fault_id: str | None = None,
    triggered: bool = False,
) -> RunResult:
    outcomes = {value.outcome for value in cases}
    if ExecutionOutcome.ERROR in outcomes:
        run_outcome = ExecutionOutcome.ERROR
    elif ExecutionOutcome.FAILED in outcomes:
        run_outcome = ExecutionOutcome.FAILED
    else:
        run_outcome = ExecutionOutcome.PASSED
    if fault_id is None:
        observation = FaultObservation(
            configured_fault_id=None,
            trigger_status=FaultTriggerStatus.NOT_CONFIGURED,
            trigger_count=0,
        )
    else:
        observation = FaultObservation(
            configured_fault_id=fault_id,
            trigger_status=(
                FaultTriggerStatus.TRIGGERED if triggered else FaultTriggerStatus.NOT_TRIGGERED
            ),
            trigger_count=1 if triggered else 0,
        )
    return RunResult(
        schema_version="2.0",
        kind="RunResult",
        run_id=run_id,
        batch_name="deepseek-suite",
        spec_id=SPEC.spec_id,
        started_at=STARTED_AT,
        finished_at=STARTED_AT + timedelta(milliseconds=3),
        duration_ms=3,
        outcome=run_outcome,
        fault=observation,
        cases=cases,
        errors=[],
    )


def suite_execution() -> SuiteExecution:
    clean_cases = [
        case("pass-case", "listItems", ExecutionOutcome.PASSED),
        case("fail-case", "getItem", ExecutionOutcome.FAILED),
        case("error-case", "createItem", ExecutionOutcome.ERROR),
    ]

    def fault_cases(
        marked_case_id: str | None,
        marked_outcome: ExecutionOutcome | None = None,
        *,
        fault_id: str,
    ) -> list[CaseResult]:
        outcomes = {
            "pass-case": ExecutionOutcome.PASSED,
            "fail-case": ExecutionOutcome.FAILED,
            "error-case": ExecutionOutcome.ERROR,
        }
        if marked_case_id is not None and marked_outcome is not None:
            outcomes[marked_case_id] = marked_outcome
        operations = {
            "pass-case": "listItems",
            "fail-case": "getItem",
            "error-case": "createItem",
        }
        return [
            case(
                case_id,
                operations[case_id],
                outcomes[case_id],
                fault_id=fault_id if case_id == marked_case_id else None,
            )
            for case_id in ("pass-case", "fail-case", "error-case")
        ]

    faults = [
        FaultRun(
            "detected-fault",
            run(
                "run-detected",
                fault_cases(
                    "pass-case",
                    ExecutionOutcome.FAILED,
                    fault_id="detected-fault",
                ),
                fault_id="detected-fault",
                triggered=True,
            ),
        ),
        FaultRun(
            "missed-fault",
            run(
                "run-missed",
                fault_cases(
                    "pass-case",
                    ExecutionOutcome.PASSED,
                    fault_id="missed-fault",
                ),
                fault_id="missed-fault",
                triggered=True,
            ),
        ),
        FaultRun(
            "untriggered-fault",
            run(
                "run-untriggered",
                fault_cases(None, fault_id="untriggered-fault"),
                fault_id="untriggered-fault",
                triggered=False,
            ),
        ),
        FaultRun(
            "ineligible-fault",
            run(
                "run-ineligible",
                fault_cases(
                    "fail-case",
                    ExecutionOutcome.FAILED,
                    fault_id="ineligible-fault",
                ),
                fault_id="ineligible-fault",
                triggered=True,
            ),
        ),
        FaultRun(
            "inconclusive-fault",
            run(
                "run-inconclusive",
                fault_cases(
                    "pass-case",
                    ExecutionOutcome.ERROR,
                    fault_id="inconclusive-fault",
                ),
                fault_id="inconclusive-fault",
                triggered=True,
            ),
        ),
    ]
    return SuiteExecution(
        suite_id="deepseek-suite",
        repetition=1,
        clean=run("run-clean", clean_cases),
        faults=tuple(faults),
    )


def generation_record() -> GenerationRecord:
    return GenerationRecord(
        schema_version="1.0",
        kind="GenerationRecord",
        generation_id="deepseek-generation",
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_version="test-cases-v1",
        provider_request_id="provider-request-1",
        finish_reason="stop",
        started_at=STARTED_AT,
        finished_at=STARTED_AT + timedelta(milliseconds=10),
        duration_ms=10,
        status=GenerationStatus.SUCCEEDED,
        request_count=1,
        token_usage=GenerationTokenUsage(input_tokens=100, output_tokens=50),
        estimated_cost_usd=0.001,
        case_admission=CaseAdmissionSummary(
            received_case_count=5,
            admitted_case_count=3,
            rejected_case_count=2,
            rejections=[
                CaseAdmissionRejection(
                    index=3,
                    case_id="rejected-one",
                    stage=CaseAdmissionStage.SEMANTIC,
                    code="case_semantics_invalid",
                    detail_codes=["unknown_operation"],
                ),
                CaseAdmissionRejection(
                    index=4,
                    case_id="rejected-two",
                    stage=CaseAdmissionStage.LIMIT,
                    code="case_step_limit_exceeded",
                    detail_codes=[],
                ),
            ],
        ),
        error=None,
    )


def test_evaluates_clean_quality_coverage_and_all_fault_outcomes() -> None:
    result = evaluate_suite_execution(
        suite_execution(),
        SPEC,
        generation_record(),
        evaluation_id="evaluation-deepseek-r1",
    )

    assert result.generator.kind is GeneratorKind.LLM
    assert result.generator.name == "deepseek"
    assert result.admission.admission_rate == pytest.approx(0.6)
    assert result.execution.executable_case_rate == pytest.approx(2 / 3)
    assert result.execution.clean_false_positive_rate == pytest.approx(0.5)
    assert result.execution.covered_operation_count == 3
    assert result.execution.eligible_operation_count == 6
    assert result.execution.operation_coverage_rate == pytest.approx(0.5)
    assert result.execution.clean_request_count == 3
    assert result.execution.fault_request_count == 15
    assert result.execution.total_request_count == 18

    outcomes = {fault.fault_id: fault.outcome for fault in result.faults}
    assert outcomes == {
        "detected-fault": FaultEvaluationOutcome.DETECTED,
        "missed-fault": FaultEvaluationOutcome.NOT_DETECTED,
        "untriggered-fault": FaultEvaluationOutcome.NOT_TRIGGERED,
        "ineligible-fault": FaultEvaluationOutcome.NO_ELIGIBLE_CASE,
        "inconclusive-fault": FaultEvaluationOutcome.INCONCLUSIVE,
    }
    detected = result.faults[0]
    assert detected.detected_case_ids == ["pass-case"]
    assert detected.first_detection_request == 1
    assert result.fault_summary.detected_fault_count == 1
    assert result.fault_summary.evaluable_fault_count == 2
    assert result.fault_summary.fault_detection_rate == pytest.approx(0.5)
    assert result.fault_summary.faults_detected_per_100_requests == pytest.approx(100 / 15)


def test_failed_oracle_detects_fault_even_when_same_case_also_errors() -> None:
    fault_id = "mixed-failure-fault"
    clean_case = case("pass-case", "getItem", ExecutionOutcome.PASSED)
    failed_step = step("getItem", ExecutionOutcome.FAILED, fault_id=fault_id)
    error_step = step("createItem", ExecutionOutcome.ERROR)
    mixed_case = CaseResult(
        case_id="pass-case",
        outcome=ExecutionOutcome.ERROR,
        steps=[failed_step, error_step],
        relations=[],
        errors=[],
    )
    execution = SuiteExecution(
        suite_id="deepseek-suite",
        repetition=1,
        clean=run("run-clean", [clean_case]),
        faults=(
            FaultRun(
                fault_id,
                run(
                    "run-mixed-failure",
                    [mixed_case],
                    fault_id=fault_id,
                    triggered=True,
                ),
            ),
        ),
    )
    source = generation_record().model_copy(
        update={
            "case_admission": CaseAdmissionSummary(
                received_case_count=1,
                admitted_case_count=1,
                rejected_case_count=0,
                rejections=[],
            )
        }
    )

    result = evaluate_suite_execution(
        execution,
        SPEC,
        source,
        evaluation_id="evaluation-mixed-failure",
    )

    fault = result.faults[0]
    assert fault.outcome is FaultEvaluationOutcome.DETECTED
    assert fault.detected_case_ids == ["pass-case"]
    assert fault.errored_case_ids == ["pass-case"]
    assert fault.first_detection_request == 1


def test_maps_schemathesis_adaptation_to_same_admission_metrics() -> None:
    record = AdaptationRecord(
        schema_version="1.0",
        kind="AdaptationRecord",
        tool="schemathesis",
        tool_version="4.25.2",
        adapter_version="schemathesis-case-v1",
        seed=0,
        received_case_count=4,
        adapted_case_count=3,
        rejected_case_count=1,
        skip_reasons=[AdaptationSkipReason(code="unsupported", count=1)],
    )

    result = evaluate_suite_execution(
        suite_execution(),
        SPEC,
        record,
        evaluation_id="evaluation-schemathesis-r1",
    )

    assert result.generator.kind is GeneratorKind.SCHEMA_TOOL
    assert result.generator.name == "schemathesis"
    assert result.generator.generation_request_count == 0
    assert result.admission.admission_rate == pytest.approx(0.75)


def test_keeps_native_admission_separate_from_shared_enhancement_execution() -> None:
    record = AdaptationRecord(
        schema_version="1.0",
        kind="AdaptationRecord",
        tool="schemathesis",
        tool_version="4.25.2",
        adapter_version="schemathesis-case-v1",
        seed=0,
        received_case_count=2,
        adapted_case_count=2,
        rejected_case_count=0,
        skip_reasons=[],
    )
    composition = SuiteCompositionRecord.model_validate(
        {
            "schema_version": "1.0",
            "kind": "SuiteCompositionRecord",
            "composition_id": "schemathesis-enhanced-r1",
            "base_batch": {"case_count": 2, "sha256": "0" * 64},
            "enhancements": [
                {
                    "pack_id": "shared-relations-v1",
                    "batch": {"case_count": 1, "sha256": "1" * 64},
                }
            ],
            "composed_batch": {"case_count": 3, "sha256": "2" * 64},
        }
    )

    result = evaluate_suite_execution(
        suite_execution(),
        SPEC,
        record,
        evaluation_id="evaluation-schemathesis-enhanced-r1",
        composition_record=composition,
    )

    assert result.admission.admitted_case_count == 2
    assert result.composition is not None
    assert result.composition.enhancement_case_count == 1
    assert result.composition.enhancement_pack_ids == ["shared-relations-v1"]
    assert result.execution.admitted_case_count == 3


def test_rejects_trigger_count_without_matching_response_evidence() -> None:
    execution = suite_execution()
    detected = execution.faults[0]
    raw = detected.result.model_dump(mode="python")
    raw["fault"]["trigger_count"] = 2
    mismatched = FaultRun(detected.fault_id, RunResult.model_validate(raw))
    invalid_execution = SuiteExecution(
        suite_id=execution.suite_id,
        repetition=execution.repetition,
        clean=execution.clean,
        faults=(mismatched, *execution.faults[1:]),
    )

    with pytest.raises(EvaluationInputError, match="does not match response evidence"):
        evaluate_suite_execution(
            invalid_execution,
            SPEC,
            generation_record(),
            evaluation_id="evaluation-invalid-r1",
        )


def test_rejects_admitted_count_that_differs_from_clean_run() -> None:
    record = AdaptationRecord(
        schema_version="1.0",
        kind="AdaptationRecord",
        tool="schemathesis",
        tool_version="4.25.2",
        adapter_version="schemathesis-case-v1",
        seed=0,
        received_case_count=2,
        adapted_case_count=2,
        rejected_case_count=0,
        skip_reasons=[],
    )

    with pytest.raises(EvaluationInputError, match="admitted case count"):
        evaluate_suite_execution(
            suite_execution(),
            SPEC,
            record,
            evaluation_id="evaluation-invalid-count-r1",
        )


def test_writes_raw_runs_and_evaluation_as_separate_artifacts(tmp_path: Path) -> None:
    execution = suite_execution()
    evaluation = evaluate_suite_execution(
        execution,
        SPEC,
        generation_record(),
        evaluation_id="evaluation-deepseek-r1",
    )

    paths = write_evaluated_suite_artifacts(
        EvaluatedSuite(execution=execution, evaluation=evaluation),
        tmp_path / "deepseek-r1",
    )

    assert json.loads(paths.clean_run.read_text(encoding="utf-8"))["kind"] == "RunResult"
    assert len(paths.fault_runs) == 5
    assert all(path.exists() for path in paths.fault_runs)
    saved_evaluation = json.loads(paths.evaluation.read_text(encoding="utf-8"))
    assert saved_evaluation["kind"] == "EvaluationResult"
    assert saved_evaluation["clean_run_id"] == "run-clean"


def test_refuses_to_overwrite_an_evaluated_suite_artifact_set(tmp_path: Path) -> None:
    execution = suite_execution()
    evaluation = evaluate_suite_execution(
        execution,
        SPEC,
        generation_record(),
        evaluation_id="evaluation-deepseek-r1",
    )
    suite = EvaluatedSuite(execution=execution, evaluation=evaluation)
    output_directory = tmp_path / "deepseek-r1"
    paths = write_evaluated_suite_artifacts(suite, output_directory)
    original = paths.clean_run.read_text(encoding="utf-8")

    with pytest.raises(SuiteArtifactError, match="refusing to overwrite"):
        write_evaluated_suite_artifacts(suite, output_directory)

    assert paths.clean_run.read_text(encoding="utf-8") == original


def test_rejects_mismatched_raw_runs_and_evaluation() -> None:
    execution = suite_execution()
    evaluation = evaluate_suite_execution(
        execution,
        SPEC,
        generation_record(),
        evaluation_id="evaluation-deepseek-r1",
    ).model_copy(update={"clean_run_id": "another-clean-run"})

    with pytest.raises(ValueError, match="reference the execution clean run"):
        EvaluatedSuite(execution=execution, evaluation=evaluation)
