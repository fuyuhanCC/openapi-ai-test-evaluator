"""Domain contracts exposed by the framework."""

from openapi_ai_test_evaluator.domain.execution import RunResult, TestCaseResult
from openapi_ai_test_evaluator.domain.generation import (
    AdaptationRecord,
    AdaptationSkipReason,
    GenerationConfig,
    GenerationRecord,
)
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec, OperationModel
from openapi_ai_test_evaluator.domain.test_case import (
    ExecutionConfig,
    TestCase,
    TestCaseBatch,
)
from openapi_ai_test_evaluator.domain.test_plan import TestPlan

__all__ = [
    "ExecutionConfig",
    "AdaptationRecord",
    "AdaptationSkipReason",
    "GenerationConfig",
    "GenerationRecord",
    "OpenAPISpec",
    "OperationModel",
    "RunResult",
    "TestCase",
    "TestCaseBatch",
    "TestCaseResult",
    "TestPlan",
]
