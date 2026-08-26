"""Load strict evaluation artifacts used by comparison reports."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from openapi_ai_test_evaluator.domain.evaluation import EvaluationResult


class EvaluationArtifactError(ValueError):
    """An EvaluationResult artifact cannot be read or validated."""


def load_evaluation_result(path: Path) -> EvaluationResult:
    """Read one strict EvaluationResult JSON artifact."""
    try:
        serialized = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise EvaluationArtifactError(f"cannot read {path}: {error}") from error
    try:
        return EvaluationResult.model_validate_json(serialized)
    except ValidationError as error:
        raise EvaluationArtifactError(f"invalid EvaluationResult {path}: {error}") from error


__all__ = ["EvaluationArtifactError", "load_evaluation_result"]
