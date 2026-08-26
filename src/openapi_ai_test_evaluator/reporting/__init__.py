"""Aggregate and render evaluation comparison reports."""

from openapi_ai_test_evaluator.reporting.artifacts import (
    EvaluationArtifactError,
    load_evaluation_result,
)
from openapi_ai_test_evaluator.reporting.comparison import (
    ComparisonInputError,
    compare_evaluations,
)
from openapi_ai_test_evaluator.reporting.markdown import render_comparison_markdown

__all__ = [
    "ComparisonInputError",
    "EvaluationArtifactError",
    "compare_evaluations",
    "load_evaluation_result",
    "render_comparison_markdown",
]
