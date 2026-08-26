from pathlib import Path

import httpx
import pytest

from openapi_ai_test_evaluator.domain.execution import (
    ExecutionOutcome,
    FaultTriggerStatus,
)
from openapi_ai_test_evaluator.domain.fault import FAULT_ID_RESPONSE_HEADER
from openapi_ai_test_evaluator.domain.generation import AdaptationRecord
from openapi_ai_test_evaluator.domain.test_case import TestCaseBatch as CaseBatch
from openapi_ai_test_evaluator.evaluation import EvaluationInputError, run_evaluated_suite
from openapi_ai_test_evaluator.evaluation.suite_runner import (
    BenchmarkControlError,
    execute_fault_suite,
)
from openapi_ai_test_evaluator.spec import load_openapi

SPEC = load_openapi(Path("examples/demo-items/openapi.yaml"))
BATCH = CaseBatch.model_validate(
    {
        "schema_version": "1.0",
        "cases": [
            {
                "id": "list-items",
                "steps": [
                    {
                        "id": "list",
                        "operation_id": "listItems",
                        "assertions": [
                            {"operator": "status_is", "expected": 200},
                            {"operator": "schema_matches"},
                        ],
                    }
                ],
            }
        ],
    }
)


class FakeBenchmark:
    def __init__(self) -> None:
        self.active_fault: str | None = None
        self.trigger_count = 0
        self.events: list[str] = []

    def handle_control(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "sut.test" and path == "/__test__/reset":
            self.events.append("reset-sut")
            return httpx.Response(204)
        if request.method == "DELETE" and path == "/__oate__/fault":
            self.active_fault = None
            self.trigger_count = 0
            self.events.append("disable-fault")
            return httpx.Response(200, json=self.state())
        if request.method == "PUT" and path.startswith("/__oate__/faults/"):
            self.active_fault = path.rsplit("/", 1)[-1]
            self.trigger_count = 0
            self.events.append(f"activate-{self.active_fault}")
            return httpx.Response(200, json=self.state())
        if request.method == "GET" and path == "/__oate__/state":
            self.events.append("read-state")
            return httpx.Response(200, json=self.state())
        return httpx.Response(404)

    def handle_execution(self, request: httpx.Request) -> httpx.Response:
        self.events.append("execute-list")
        if self.active_fault == "status-fault":
            self.trigger_count += 1
            return httpx.Response(
                500,
                headers={FAULT_ID_RESPONSE_HEADER: "status-fault"},
                json={"items": [], "offset": 0, "limit": 20, "total": 0},
            )
        return httpx.Response(
            200,
            json={"items": [], "offset": 0, "limit": 20, "total": 0},
        )

    def state(self) -> dict[str, object]:
        if self.active_fault is None:
            return {
                "mode": "pass_through",
                "configured_fault_id": None,
                "trigger_count": 0,
            }
        return {
            "mode": "active",
            "configured_fault_id": self.active_fault,
            "trigger_count": self.trigger_count,
        }


def test_executes_same_batch_clean_then_once_per_fault() -> None:
    benchmark = FakeBenchmark()
    execution = execute_fault_suite(
        BATCH,
        SPEC,
        suite_id="schemathesis",
        repetition=1,
        runner_base_url="http://proxy.test",
        proxy_control_url="http://proxy.test",
        sut_reset_url="http://sut.test/__test__/reset",
        fault_ids=["status-fault", "unreached-fault"],
        execution_transport=httpx.MockTransport(benchmark.handle_execution),
        control_transport=httpx.MockTransport(benchmark.handle_control),
    )

    assert execution.clean.outcome is ExecutionOutcome.PASSED
    assert [fault.fault_id for fault in execution.faults] == [
        "status-fault",
        "unreached-fault",
    ]
    assert execution.faults[0].result.outcome is ExecutionOutcome.FAILED
    assert execution.faults[0].result.fault.trigger_status is FaultTriggerStatus.TRIGGERED
    assert execution.faults[0].result.fault.trigger_count == 1
    assert execution.faults[1].result.outcome is ExecutionOutcome.PASSED
    assert execution.faults[1].result.fault.trigger_status is FaultTriggerStatus.NOT_TRIGGERED
    assert benchmark.events == [
        "disable-fault",
        "reset-sut",
        "execute-list",
        "disable-fault",
        "reset-sut",
        "activate-status-fault",
        "execute-list",
        "read-state",
        "disable-fault",
        "reset-sut",
        "activate-unreached-fault",
        "execute-list",
        "read-state",
        "disable-fault",
    ]


def test_rejects_duplicate_fault_ids_before_control_requests() -> None:
    with pytest.raises(ValueError, match="fault IDs must be unique"):
        execute_fault_suite(
            BATCH,
            SPEC,
            suite_id="deepseek",
            repetition=1,
            runner_base_url="http://proxy.test",
            proxy_control_url="http://proxy.test",
            sut_reset_url="http://sut.test/__test__/reset",
            fault_ids=["same-fault", "same-fault"],
        )


def test_connects_one_suite_execution_to_its_evaluation() -> None:
    benchmark = FakeBenchmark()
    evaluated = run_evaluated_suite(
        BATCH,
        SPEC,
        AdaptationRecord(
            schema_version="1.0",
            kind="AdaptationRecord",
            tool="schemathesis",
            tool_version="4.25.2",
            adapter_version="schemathesis-case-v1",
            seed=7,
            duration_ms=2,
            received_case_count=1,
            adapted_case_count=1,
            rejected_case_count=0,
            skip_reasons=[],
        ),
        suite_id="schemathesis",
        repetition=1,
        evaluation_id="evaluation-schemathesis-r1",
        runner_base_url="http://proxy.test",
        proxy_control_url="http://proxy.test",
        sut_reset_url="http://sut.test/__test__/reset",
        fault_ids=["status-fault"],
        execution_transport=httpx.MockTransport(benchmark.handle_execution),
        control_transport=httpx.MockTransport(benchmark.handle_control),
    )

    assert evaluated.execution.clean.run_id == "schemathesis-r1-clean"
    assert evaluated.evaluation.clean_run_id == evaluated.execution.clean.run_id
    assert evaluated.evaluation.generator.name == "schemathesis"
    assert evaluated.evaluation.fault_summary.detected_fault_count == 1
    assert evaluated.evaluation.faults[0].run_id == "schemathesis-r1-status-fault"


def test_rejects_source_record_count_before_any_control_request() -> None:
    with pytest.raises(EvaluationInputError, match="does not match the frozen batch"):
        run_evaluated_suite(
            BATCH,
            SPEC,
            AdaptationRecord(
                schema_version="1.0",
                kind="AdaptationRecord",
                tool="schemathesis",
                tool_version="4.25.2",
                adapter_version="schemathesis-case-v1",
                seed=7,
                received_case_count=2,
                adapted_case_count=2,
                rejected_case_count=0,
                skip_reasons=[],
            ),
            suite_id="schemathesis",
            repetition=1,
            evaluation_id="evaluation-schemathesis-r1",
            runner_base_url="http://proxy.test",
            proxy_control_url="http://proxy.test",
            sut_reset_url="http://sut.test/__test__/reset",
            fault_ids=["status-fault"],
        )


def test_rejects_invalid_proxy_state_document() -> None:
    def control_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"mode": "unexpected"})

    with pytest.raises(BenchmarkControlError, match="invalid state document"):
        execute_fault_suite(
            BATCH,
            SPEC,
            suite_id="deepseek",
            repetition=1,
            runner_base_url="http://proxy.test",
            proxy_control_url="http://proxy.test",
            sut_reset_url="http://sut.test/__test__/reset",
            fault_ids=[],
            control_transport=httpx.MockTransport(control_handler),
        )
