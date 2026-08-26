"""Admit LLM-produced cases independently without repairing provider output."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from openapi_ai_test_evaluator.domain.generation import (
    CaseAdmissionRejection,
    CaseAdmissionStage,
    CaseAdmissionSummary,
    GenerationConfig,
)
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.domain.test_case import TestCase, TestCaseBatch
from openapi_ai_test_evaluator.validation import validate_test_case_batch_semantics


class ProviderOutputAdmissionError(ValueError):
    """Provider output does not contain a decodable TestCaseBatch envelope."""


@dataclass(frozen=True, slots=True)
class GeneratedCaseAdmission:
    batch: TestCaseBatch | None
    summary: CaseAdmissionSummary


def admit_generated_cases(
    output_text: str,
    spec: OpenAPISpec,
    config: GenerationConfig,
) -> GeneratedCaseAdmission:
    """Validate each decoded case independently and preserve rejection metrics."""
    raw_cases = _decode_case_list(output_text)
    admitted_cases: list[TestCase] = []
    rejections: list[CaseAdmissionRejection] = []
    seen_case_ids: set[str] = set()

    for index, raw_case in enumerate(raw_cases):
        case_id = _raw_case_id(raw_case)
        if index >= config.max_cases:
            rejections.append(
                _rejection(
                    index,
                    case_id,
                    CaseAdmissionStage.LIMIT,
                    "case_count_limit_exceeded",
                )
            )
            continue

        try:
            case = TestCase.model_validate(raw_case)
        except ValidationError as error:
            detail_codes = sorted({str(detail["type"]) for detail in error.errors()})
            rejections.append(
                _rejection(
                    index,
                    case_id,
                    CaseAdmissionStage.STRUCTURE,
                    "case_structure_invalid",
                    detail_codes,
                )
            )
            continue

        if case.id in seen_case_ids:
            rejections.append(
                _rejection(
                    index,
                    case.id,
                    CaseAdmissionStage.STRUCTURE,
                    "duplicate_case_id",
                )
            )
            continue
        seen_case_ids.add(case.id)

        step_count = len(case.setup) + len(case.steps) + len(case.cleanup)
        if step_count > config.max_steps_per_case:
            rejections.append(
                _rejection(
                    index,
                    case.id,
                    CaseAdmissionStage.LIMIT,
                    "case_step_limit_exceeded",
                )
            )
            continue

        provisional_batch = TestCaseBatch(schema_version="1.0", cases=[case])
        semantic_issues = validate_test_case_batch_semantics(provisional_batch, spec)
        if semantic_issues:
            rejections.append(
                _rejection(
                    index,
                    case.id,
                    CaseAdmissionStage.SEMANTIC,
                    "case_semantics_invalid",
                    sorted({issue.code for issue in semantic_issues}),
                )
            )
            continue

        admitted_cases.append(case)

    summary = CaseAdmissionSummary(
        received_case_count=len(raw_cases),
        admitted_case_count=len(admitted_cases),
        rejected_case_count=len(rejections),
        rejections=rejections,
    )
    batch = (
        TestCaseBatch(schema_version="1.0", cases=admitted_cases) if admitted_cases else None
    )
    return GeneratedCaseAdmission(batch=batch, summary=summary)


def _decode_case_list(output_text: str) -> list[object]:
    try:
        raw_batch = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise ProviderOutputAdmissionError("provider output is not valid JSON") from error
    if not isinstance(raw_batch, dict):
        raise ProviderOutputAdmissionError("provider output must be a JSON object")
    if set(raw_batch) != {"schema_version", "cases"}:
        raise ProviderOutputAdmissionError(
            "provider output must contain only schema_version and cases"
        )
    if raw_batch["schema_version"] != "1.0":
        raise ProviderOutputAdmissionError("provider output schema_version must be '1.0'")
    raw_cases = raw_batch["cases"]
    if not isinstance(raw_cases, list):
        raise ProviderOutputAdmissionError("provider output cases must be a list")
    return raw_cases


def _raw_case_id(raw_case: object) -> str | None:
    if not isinstance(raw_case, dict):
        return None
    case_id = raw_case.get("id")
    return case_id if isinstance(case_id, str) and case_id.strip() else None


def _rejection(
    index: int,
    case_id: str | None,
    stage: CaseAdmissionStage,
    code: str,
    detail_codes: list[str] | None = None,
) -> CaseAdmissionRejection:
    return CaseAdmissionRejection(
        index=index,
        case_id=case_id,
        stage=stage,
        code=code,
        detail_codes=detail_codes or [],
    )
