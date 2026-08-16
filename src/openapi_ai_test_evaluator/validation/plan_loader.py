"""YAML loading for TestPlan contracts."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from openapi_ai_test_evaluator.domain import TestPlan


class PlanLoadError(ValueError):
    """A TestPlan could not be decoded or validated."""


def load_test_plan(path: Path) -> TestPlan:
    """Load and structurally validate a TestPlan YAML file."""
    try:
        raw_plan = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PlanLoadError(f"cannot read {path}: {error}") from error
    except yaml.YAMLError as error:
        raise PlanLoadError(f"invalid YAML in {path}: {error}") from error

    if not isinstance(raw_plan, dict):
        raise PlanLoadError(f"{path} must contain a YAML mapping at the top level")

    try:
        return TestPlan.model_validate(raw_plan)
    except ValidationError as error:
        raise PlanLoadError(str(error)) from error
