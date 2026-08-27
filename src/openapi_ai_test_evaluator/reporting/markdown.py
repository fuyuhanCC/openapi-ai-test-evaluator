"""Render deterministic human-readable Markdown comparison reports."""

from __future__ import annotations

from openapi_ai_test_evaluator.domain.reporting import (
    ComparisonResult,
    MetricStatistics,
    SuiteComparison,
)


def render_comparison_markdown(comparison: ComparisonResult) -> str:
    """Render key raw and normalized metrics without declaring a winner."""
    lines = [
        f"# API Test Generation Comparison: {comparison.comparison_id}",
        "",
        f"- OpenAPI spec: `{comparison.spec_id}`",
        f"- Mode: `{comparison.mode.value}`",
        f"- Paired repetitions: {', '.join(map(str, comparison.repetitions))}",
        f"- Faults: {len(comparison.fault_ids)}",
        "",
        "## Suite Summary",
        "",
        (
            "| Suite | Generator | Cases received / native admitted | "
            "Cases executed / shared | Admission | Operation coverage | Clean false positives | "
            "Fault detection | Detected / 100 fault requests | Total requests | "
            "Generation calls | Input / output tokens | Generation time | Execution time | "
            "Estimated API cost |"
        ),
        (
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
            "---: | ---: | ---: | ---: | ---: | ---: |"
        ),
    ]
    for suite in comparison.suites:
        lines.append(_suite_row(suite))

    lines.extend(
        [
            "",
            "## Per-Fault Stability",
            "",
            _fault_header(comparison),
            _fault_separator(comparison),
        ]
    )
    suites_by_id = {suite.suite_id: suite for suite in comparison.suites}
    for fault_id in comparison.fault_ids:
        cells = [f"`{fault_id}`"]
        for suite in comparison.suites:
            fault = next(
                item for item in suites_by_id[suite.suite_id].faults if item.fault_id == fault_id
            )
            cells.append(
                _rate_count(fault.detected_count, fault.evaluable_count)
                + f"; outcomes D/M/T/E/I={fault.detected_count}/"
                f"{fault.not_detected_count}/{fault.not_triggered_count}/"
                f"{fault.no_eligible_case_count}/{fault.inconclusive_count}"
            )
        lines.append(f"| {' | '.join(cells)} |")

    lines.extend(
        [
            "",
            "## Interpretation Notes",
            "",
            (
                "- Native admission belongs to the generator; shared cases are added only in "
                "augmented arms and are reported separately."
            ),
            "- Suite sizes are intentionally not equalized; raw request counts are reported.",
            (
                "- Fault detection rate uses only triggered faults with a deterministic "
                "eligible verdict."
            ),
            (
                "- `D/M/T/E/I` means detected, missed, not triggered, no eligible case, "
                "and inconclusive."
            ),
            (
                "- `n/a` means the source did not report that metric or no evaluable "
                "denominator existed."
            ),
            "",
            "## Source Evaluations",
            "",
        ]
    )
    for suite in comparison.suites:
        identifiers = ", ".join(f"`{value}`" for value in suite.evaluation_ids)
        lines.append(f"- `{suite.suite_id}`: {identifiers}")
    return "\n".join(lines) + "\n"


def _suite_row(suite: SuiteComparison) -> str:
    generator = suite.generator.name
    if suite.generator.model is not None:
        generator = f"{generator} / {suite.generator.model}"
    return (
        "| "
        + " | ".join(
            [
                f"`{suite.suite_id}`",
                generator,
                _metric_pair(suite.received_case_count, suite.admitted_case_count),
                _metric_pair(suite.executed_case_count, suite.enhancement_case_count),
                _percent(suite.admission_rate),
                _percent(suite.operation_coverage_rate),
                _percent(suite.clean_false_positive_rate),
                _percent(suite.fault_detection_rate),
                _number(suite.faults_detected_per_100_requests, digits=2),
                _number(suite.total_request_count),
                _number(suite.generation_request_count),
                _metric_pair(suite.input_tokens, suite.output_tokens),
                _duration(suite.generation_duration_ms),
                _duration(suite.execution_duration_ms),
                _currency(suite.estimated_cost_usd),
            ]
        )
        + " |"
    )


def _fault_header(comparison: ComparisonResult) -> str:
    return "| Fault | " + " | ".join(f"`{suite.suite_id}`" for suite in comparison.suites) + " |"


def _fault_separator(comparison: ComparisonResult) -> str:
    return "| --- | " + " | ".join("---" for _ in comparison.suites) + " |"


def _percent(metric: MetricStatistics) -> str:
    if metric.mean is None:
        return "n/a"
    return _mean_std(metric, scale=100, suffix="%", digits=1)


def _number(metric: MetricStatistics, *, digits: int = 1) -> str:
    if metric.mean is None:
        return "n/a"
    return _mean_std(metric, scale=1, suffix="", digits=digits)


def _duration(metric: MetricStatistics) -> str:
    if metric.mean is None:
        return "n/a"
    return _mean_std(metric, scale=1, suffix=" ms", digits=1)


def _currency(metric: MetricStatistics) -> str:
    if metric.mean is None:
        return "n/a"
    return _mean_std(metric, scale=1, suffix=" USD", prefix="$", digits=6)


def _metric_pair(left: MetricStatistics, right: MetricStatistics) -> str:
    return f"{_number(left)} / {_number(right)}"


def _mean_std(
    metric: MetricStatistics,
    *,
    scale: float,
    suffix: str,
    digits: int,
    prefix: str = "",
) -> str:
    assert metric.mean is not None
    assert metric.stddev is not None
    mean = metric.mean * scale
    stddev = metric.stddev * scale
    formatted = f"{prefix}{mean:.{digits}f}{suffix}"
    if metric.sample_count > 1:
        formatted += f" ± {stddev:.{digits}f}{suffix}"
    if metric.missing_count:
        formatted += f" (missing {metric.missing_count})"
    return formatted


def _rate_count(detected: int, evaluable: int) -> str:
    if evaluable == 0:
        return "n/a"
    return f"{detected / evaluable * 100:.1f}% ({detected}/{evaluable})"


__all__ = ["render_comparison_markdown"]
