from pathlib import Path

import pytest

from openapi_ai_test_evaluator.domain.benchmark import BenchmarkConfig, BenchmarkSuiteArm
from openapi_ai_test_evaluator.evaluation import (
    BenchmarkConfigLoadError,
    load_benchmark_config,
)

ROOT = Path(__file__).parents[2]


def valid_config() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "kind": "BenchmarkConfig",
        "benchmark_id": "demo-four-arm",
        "spec": "openapi.yaml",
        "repetitions": [1, 2],
        "fault_ids": ["status-fault", "body-fault"],
        "endpoints": {
            "runner_base_url": "http://proxy.test",
            "proxy_control_url": "http://proxy.test",
            "sut_reset_url": "http://sut.test/__test__/reset",
        },
        "execution": {"timeout_ms": 3000, "allow_mutations": True},
        "suites": [
            {
                "suite_id": "deepseek-native",
                "arm": "native",
                "inputs": [
                    {
                        "repetition": repetition,
                        "cases": f"deepseek-r{repetition}.json",
                        "source_record": f"generation-r{repetition}.json",
                    }
                    for repetition in (1, 2)
                ],
            },
            {
                "suite_id": "deepseek-enhanced",
                "arm": "enhanced",
                "inputs": [
                    {
                        "repetition": repetition,
                        "cases": f"deepseek-enhanced-r{repetition}.json",
                        "source_record": f"generation-r{repetition}.json",
                        "composition_record": f"composition-r{repetition}.json",
                    }
                    for repetition in (1, 2)
                ],
            },
        ],
        "output_directory": "runs",
        "report": {
            "comparison_id": "demo-comparison",
            "json_output": "comparison.json",
            "markdown_output": "comparison.md",
        },
    }


def test_models_paired_native_and_enhanced_repetitions() -> None:
    config = BenchmarkConfig.model_validate(valid_config())

    assert config.repetitions == [1, 2]
    assert config.suites[0].arm is BenchmarkSuiteArm.NATIVE
    assert config.suites[1].arm is BenchmarkSuiteArm.ENHANCED
    assert config.execution.allow_mutations is True


def test_rejects_suite_that_omits_a_paired_repetition() -> None:
    raw = valid_config()
    suites = raw["suites"]
    assert isinstance(suites, list)
    suites[0]["inputs"].pop()  # type: ignore[index,union-attr]

    with pytest.raises(ValueError, match="must match benchmark repetitions"):
        BenchmarkConfig.model_validate(raw)


def test_rejects_native_suite_with_a_composition_record() -> None:
    raw = valid_config()
    suites = raw["suites"]
    assert isinstance(suites, list)
    suites[0]["inputs"][0]["composition_record"] = "unexpected.json"  # type: ignore[index]

    with pytest.raises(ValueError, match="native suites cannot contain"):
        BenchmarkConfig.model_validate(raw)


def test_loads_the_demo_four_arm_config() -> None:
    config = load_benchmark_config(ROOT / "benchmarks/demo_items/pilot-four-arm.yaml")

    assert config.benchmark_id == "demo-items-four-arm-pilot"
    assert len(config.suites) == 4
    assert config.fault_ids[-1] == "list-duplicate-first-item"


def test_loads_final_config_with_explicit_repetition_pricing() -> None:
    config = load_benchmark_config(
        ROOT / "benchmarks/demo_items/final-four-arm-v5.yaml"
    )

    assert {snapshot.rate_class for snapshot in config.pricing} == {"peak", "off-peak"}
    deepseek = config.suites[0]
    assert [item.pricing_id for item in deepseek.inputs] == [
        "deepseek-v4-flash-2026-08-peak",
        "deepseek-v4-flash-2026-08-peak",
        "deepseek-v4-flash-2026-08-off-peak",
    ]
    assert all(item.pricing_id is None for item in config.suites[2].inputs)


def test_rejects_unknown_pricing_reference() -> None:
    raw = valid_config()
    suites = raw["suites"]
    assert isinstance(suites, list)
    suites[0]["inputs"][0]["pricing_id"] = "unknown-price"  # type: ignore[index]

    with pytest.raises(ValueError, match="references unknown pricing"):
        BenchmarkConfig.model_validate(raw)


def test_reports_malformed_yaml_as_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "benchmark.yaml"
    path.write_text("suites: [\n", encoding="utf-8")

    with pytest.raises(BenchmarkConfigLoadError, match="invalid YAML"):
        load_benchmark_config(path)
