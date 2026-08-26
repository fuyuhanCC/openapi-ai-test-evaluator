"""Domain contracts exposed by the framework."""

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
    "CaseAdmissionRejection",
    "CaseAdmissionStage",
    "CaseAdmissionSummary",
    "GenerationConfig",
    "GenerationRecord",
    "FaultDefinition",
    "FaultProxyState",
    "OpenAPISpec",
    "OperationModel",
    "RunResult",
    "TestCase",
    "TestCaseBatch",
    "TestCaseResult",
    "TestPlan",
]
