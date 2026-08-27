"""Load a strict multi-suite benchmark YAML document."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from openapi_ai_test_evaluator.domain.benchmark import BenchmarkConfig


class BenchmarkConfigLoadError(ValueError):
    """A benchmark configuration cannot be read or validated."""


def load_benchmark_config(path: Path) -> BenchmarkConfig:
    """Read and validate one BenchmarkConfig YAML artifact."""
    try:
        serialized = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise BenchmarkConfigLoadError(f"cannot read {path}: {error}") from error
    try:
        document = yaml.safe_load(serialized)
    except yaml.YAMLError as error:
        raise BenchmarkConfigLoadError(f"invalid YAML in {path}: {error}") from error
    try:
        return BenchmarkConfig.model_validate(document)
    except ValidationError as error:
        raise BenchmarkConfigLoadError(f"invalid benchmark config {path}: {error}") from error


__all__ = ["BenchmarkConfigLoadError", "load_benchmark_config"]
