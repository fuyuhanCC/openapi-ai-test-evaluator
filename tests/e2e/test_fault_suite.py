from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import httpx
import uvicorn
from fastapi import FastAPI

from openapi_ai_test_evaluator.domain.execution import (
    ExecutionOutcome,
    FaultTriggerStatus,
)
from openapi_ai_test_evaluator.domain.generation import AdaptationRecord
from openapi_ai_test_evaluator.domain.test_case import TestCaseBatch as CaseBatch
from openapi_ai_test_evaluator.evaluation import run_evaluated_suite
from openapi_ai_test_evaluator.spec import load_openapi
from services.demo_items.app import app as demo_app
from services.demo_items.app import reset_state
from services.fault_proxy.app import create_app
from services.fault_proxy.catalog import load_fault_catalog

PROJECT_ROOT = Path(__file__).parents[2]
SPEC = load_openapi(PROJECT_ROOT / "examples" / "demo-items" / "openapi.yaml")
FAULTS = load_fault_catalog(PROJECT_ROOT / "benchmarks" / "demo_items" / "faults")

BATCH = CaseBatch.model_validate(
    {
        "schema_version": "1.0",
        "cases": [
            {
                "id": "create-and-get",
                "steps": [
                    {
                        "id": "create",
                        "operation_id": "createItem",
                        "request": {"body": {"name": "pencil", "price": 1.5, "status": "active"}},
                        "extract": [
                            {
                                "variable": "item_id",
                                "source": "response.body",
                                "pointer": "/id",
                            }
                        ],
                        "assertions": [
                            {"operator": "status_is", "expected": 201},
                            {"operator": "schema_matches"},
                        ],
                    },
                    {
                        "id": "get",
                        "operation_id": "getItem",
                        "request": {"path": {"itemId": {"$var": "item_id"}}},
                        "assertions": [
                            {"operator": "status_is", "expected": 200},
                            {"operator": "schema_matches"},
                        ],
                    },
                ],
            },
            {
                "id": "create-and-list",
                "steps": [
                    {
                        "id": "create",
                        "operation_id": "createItem",
                        "request": {"body": {"name": "eraser", "price": 2.0, "status": "active"}},
                        "assertions": [
                            {"operator": "status_is", "expected": 201},
                            {"operator": "schema_matches"},
                        ],
                    },
                    {
                        "id": "list",
                        "operation_id": "listItems",
                        "assertions": [
                            {"operator": "status_is", "expected": 200},
                            {"operator": "schema_matches"},
                        ],
                    },
                ],
            },
        ],
    }
)


@contextmanager
def run_server(application: FastAPI) -> Iterator[str]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(application, log_level="error"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()

    deadline = monotonic() + 5
    while not server.started and thread.is_alive() and monotonic() < deadline:
        sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        raise RuntimeError("test server did not start within 5 seconds")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("test server did not stop within 5 seconds")


def test_runs_one_frozen_batch_through_clean_and_all_fault_states() -> None:
    reset_state()
    with run_server(demo_app) as demo_url:
        proxy_app = create_app(demo_url, faults=FAULTS)
        with run_server(proxy_app) as proxy_url:
            evaluated = run_evaluated_suite(
                BATCH,
                SPEC,
                AdaptationRecord(
                    schema_version="1.0",
                    kind="AdaptationRecord",
                    tool="schemathesis",
                    tool_version="4.25.2",
                    adapter_version="reference-adapter-v1",
                    seed=0,
                    received_case_count=2,
                    adapted_case_count=2,
                    rejected_case_count=0,
                    skip_reasons=[],
                ),
                suite_id="reference-suite",
                repetition=1,
                evaluation_id="evaluation-reference-r1",
                runner_base_url=proxy_url,
                proxy_control_url=proxy_url,
                sut_reset_url=f"{demo_url}/__test__/reset",
                fault_ids=[fault.fault_id for fault in FAULTS],
                allow_mutations=True,
            )
            final_state = httpx.get(f"{proxy_url}/__oate__/state", trust_env=False)

    execution = evaluated.execution
    evaluation = evaluated.evaluation

    assert execution.clean.outcome is ExecutionOutcome.PASSED
    assert all(
        fault.result.fault.trigger_status is FaultTriggerStatus.TRIGGERED
        for fault in execution.faults
    )
    outcomes = {fault.fault_id: fault.result.outcome for fault in execution.faults}
    assert outcomes == {
        "get-id-as-string": ExecutionOutcome.FAILED,
        "get-missing-name": ExecutionOutcome.FAILED,
        "get-status-error": ExecutionOutcome.FAILED,
        "list-duplicate-first-item": ExecutionOutcome.PASSED,
    }
    assert evaluation.fault_summary.detected_fault_count == 3
    assert evaluation.fault_summary.not_detected_fault_count == 1
    assert evaluation.fault_summary.fault_detection_rate == 0.75
    assert evaluation.execution.operation_coverage_rate == 0.5
    assert evaluation.execution.clean_request_count == 4
    assert evaluation.execution.fault_request_count == 16
    assert final_state.json() == {
        "mode": "pass_through",
        "configured_fault_id": None,
        "trigger_count": 0,
    }
