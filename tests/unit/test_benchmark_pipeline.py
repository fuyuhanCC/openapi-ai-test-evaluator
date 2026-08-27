from pathlib import Path

import httpx
import pytest

from openapi_ai_test_evaluator.domain.benchmark import BenchmarkConfig
from openapi_ai_test_evaluator.domain.fault import FAULT_ID_RESPONSE_HEADER
from openapi_ai_test_evaluator.domain.generation import AdaptationRecord
from openapi_ai_test_evaluator.evaluation import BenchmarkRunError, run_benchmark_config

ROOT = Path(__file__).parents[2]


class FakeBenchmarkServices:
    def __init__(self) -> None:
        self.active_fault: str | None = None
        self.trigger_count = 0
        self.control_events: list[str] = []

    def control(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "sut.test" and request.url.path == "/__test__/reset":
            self.control_events.append("reset")
            return httpx.Response(204)
        if request.method == "DELETE" and request.url.path == "/__oate__/fault":
            self.active_fault = None
            self.trigger_count = 0
            self.control_events.append("disable")
            return httpx.Response(200, json=self.state())
        if request.method == "PUT" and request.url.path.startswith("/__oate__/faults/"):
            self.active_fault = request.url.path.rsplit("/", 1)[-1]
            self.trigger_count = 0
            self.control_events.append("activate")
            return httpx.Response(200, json=self.state())
        if request.method == "GET" and request.url.path == "/__oate__/state":
            self.control_events.append("state")
            return httpx.Response(200, json=self.state())
        return httpx.Response(404)

    def execute(self, request: httpx.Request) -> httpx.Response:
        body = {"items": [], "offset": 0, "limit": 20, "total": 0}
        if self.active_fault == "status-fault":
            self.trigger_count += 1
            return httpx.Response(
                500,
                headers={FAULT_ID_RESPONSE_HEADER: "status-fault"},
                json=body,
            )
        return httpx.Response(200, json=body)

    def state(self) -> dict[str, object]:
        return {
            "mode": "active" if self.active_fault is not None else "pass_through",
            "configured_fault_id": self.active_fault,
            "trigger_count": self.trigger_count,
        }


def write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    cases = tmp_path / "minimal-get.yaml"
    cases.write_text(
        (ROOT / "examples/cases/minimal-get.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    source = tmp_path / "adaptation.json"
    record = AdaptationRecord(
        schema_version="1.0",
        kind="AdaptationRecord",
        tool="schemathesis",
        tool_version="4.25.2",
        adapter_version="schemathesis-case-v1",
        seed=7,
        received_case_count=1,
        adapted_case_count=1,
        rejected_case_count=0,
        skip_reasons=[],
    )
    source.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return cases, source


def config(tmp_path: Path, cases: Path, source: Path) -> BenchmarkConfig:
    return BenchmarkConfig.model_validate(
        {
            "schema_version": "1.0",
            "kind": "BenchmarkConfig",
            "benchmark_id": "two-suite-smoke",
            "spec": str(ROOT / "examples/demo-items/openapi.yaml"),
            "repetitions": [1],
            "fault_ids": ["status-fault"],
            "endpoints": {
                "runner_base_url": "http://proxy.test",
                "proxy_control_url": "http://proxy.test",
                "sut_reset_url": "http://sut.test/__test__/reset",
            },
            "execution": {"allow_mutations": False},
            "suites": [
                {
                    "suite_id": suite_id,
                    "arm": "native",
                    "inputs": [
                        {
                            "repetition": 1,
                            "cases": str(cases),
                            "source_record": str(source),
                        }
                    ],
                }
                for suite_id in ("baseline-one", "baseline-two")
            ],
            "output_directory": str(tmp_path / "runs"),
            "report": {
                "comparison_id": "two-suite-comparison",
                "json_output": str(tmp_path / "reports/comparison.json"),
                "markdown_output": str(tmp_path / "reports/comparison.md"),
            },
        }
    )


def test_runs_every_suite_then_writes_one_comparison(tmp_path: Path) -> None:
    cases, source = write_inputs(tmp_path)
    benchmark = FakeBenchmarkServices()

    result = run_benchmark_config(
        config(tmp_path, cases, source),
        tmp_path / "benchmark.yaml",
        execution_transport=httpx.MockTransport(benchmark.execute),
        control_transport=httpx.MockTransport(benchmark.control),
    )

    assert len(result.evaluations) == 2
    assert len(result.suites) == 2
    assert all(item.fault_summary.detected_fault_count == 1 for item in result.evaluations)
    assert all(item.paths.evaluation.exists() for item in result.suites)
    assert result.comparison_json.exists()
    assert result.comparison_markdown.exists()
    assert "two-suite-comparison" in result.comparison_markdown.read_text(encoding="utf-8")


def test_preflights_every_input_before_sending_control_requests(tmp_path: Path) -> None:
    cases, source = write_inputs(tmp_path)
    benchmark = FakeBenchmarkServices()
    raw = config(tmp_path, cases, source).model_dump(mode="json")
    raw["suites"][1]["inputs"][0]["source_record"] = str(tmp_path / "missing.json")
    invalid = BenchmarkConfig.model_validate(raw)

    with pytest.raises(BenchmarkRunError, match="cannot read") as captured:
        run_benchmark_config(
            invalid,
            tmp_path / "benchmark.yaml",
            execution_transport=httpx.MockTransport(benchmark.execute),
            control_transport=httpx.MockTransport(benchmark.control),
        )

    assert captured.value.stage == "suite-input"
    assert captured.value.suite_id == "baseline-two"
    assert benchmark.control_events == []
