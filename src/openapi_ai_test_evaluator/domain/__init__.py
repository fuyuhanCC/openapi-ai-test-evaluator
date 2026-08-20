"""Domain contracts exposed by the framework."""

from openapi_ai_test_evaluator.domain.execution import RunResult
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec, OperationModel
from openapi_ai_test_evaluator.domain.test_plan import TestPlan

__all__ = ["OpenAPISpec", "OperationModel", "RunResult", "TestPlan"]
