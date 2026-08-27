"""Domain contracts exposed by the framework."""

from openapi_ai_test_evaluator.domain.benchmark import BenchmarkConfig
from openapi_ai_test_evaluator.domain.composition import SuiteCompositionRecord
from openapi_ai_test_evaluator.domain.evaluation import EvaluationResult
from openapi_ai_test_evaluator.domain.execution import RunResult, TestCaseResult
from openapi_ai_test_evaluator.domain.fault import FaultDefinition, FaultProxyState
from openapi_ai_test_evaluator.domain.generation import (
    AdaptationRecord,
    AdaptationSkipReason,
    CaseAdmissionRejection,
    CaseAdmissionStage,
    CaseAdmissionSummary,
    GenerationConfig,
    GenerationRecord,
)
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec, OperationModel
from openapi_ai_test_evaluator.domain.reporting import ComparisonResult
from openapi_ai_test_evaluator.domain.test_case import (
    ExecutionConfig,
    TestCase,
    TestCaseBatch,
)
from openapi_ai_test_evaluator.domain.test_plan import TestPlan

__all__ = [
    "ExecutionConfig",
    "BenchmarkConfig",
    "EvaluationResult",
    "AdaptationRecord",
    "AdaptationSkipReason",
    "CaseAdmissionRejection",
    "CaseAdmissionStage",
    "CaseAdmissionSummary",
    "ComparisonResult",
    "GenerationConfig",
    "GenerationRecord",
    "FaultDefinition",
    "FaultProxyState",
    "OpenAPISpec",
    "OperationModel",
    "RunResult",
    "SuiteCompositionRecord",
    "TestCase",
    "TestCaseBatch",
    "TestCaseResult",
    "TestPlan",
]
