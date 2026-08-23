"""Execute runner-ready test case batches through the existing HTTP pipeline."""

from __future__ import annotations

from collections.abc import Mapping

import httpx
from pydantic import JsonValue

from openapi_ai_test_evaluator.domain.execution import FaultObservation, RunResult
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.domain.test_case import ExecutionConfig, TestCaseBatch
from openapi_ai_test_evaluator.execution.plan_executor import execute_validated_cases
from openapi_ai_test_evaluator.execution.transport import DEFAULT_MAX_RESPONSE_BYTES
from openapi_ai_test_evaluator.validation.semantic_validator import (
    SemanticIssue,
    validate_test_case_batch_semantics,
)


class CaseBatchExecutionRejected(ValueError):
    """A case batch failed semantic validation before any request was sent."""

    def __init__(self, issues: list[SemanticIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(f"TestCaseBatch has {len(issues)} semantic issue(s)")


def execute_test_case_batch(
    batch: TestCaseBatch,
    spec: OpenAPISpec,
    base_url: str,
    *,
    batch_name: str = "generated-cases",
    timeout_ms: int = 5000,
    headers: Mapping[str, str] | None = None,
    initial_variables: Mapping[str, JsonValue] | None = None,
    run_id: str | None = None,
    fault: FaultObservation | None = None,
    allow_mutations: bool = False,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    httpx_transport: httpx.BaseTransport | None = None,
) -> RunResult:
    """Validate and execute generated cases without constructing a TestPlan."""
    default_headers = dict(headers or {})
    variables = dict(initial_variables or {})
    config = ExecutionConfig(
        timeout_ms=timeout_ms,
        headers=default_headers,
        initial_variables=variables,
    )
    if semantic_issues := validate_test_case_batch_semantics(
        batch,
        spec,
        config=config,
    ):
        raise CaseBatchExecutionRejected(semantic_issues)

    return execute_validated_cases(
        batch.cases,
        spec,
        base_url,
        run_name=batch_name,
        config=config,
        run_id=run_id,
        fault=fault,
        allow_mutations=allow_mutations,
        max_response_bytes=max_response_bytes,
        httpx_transport=httpx_transport,
    )
