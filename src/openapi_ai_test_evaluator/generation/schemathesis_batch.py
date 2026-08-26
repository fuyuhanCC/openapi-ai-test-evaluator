"""Collect Schemathesis cases into one runner-ready batch and adaptation record."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import schemathesis
from schemathesis import Case

from openapi_ai_test_evaluator.domain.generation import (
    AdaptationRecord,
    AdaptationSkipReason,
)
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.domain.test_case import TestCase, TestCaseBatch
from openapi_ai_test_evaluator.generation.schemathesis_adapter import (
    AdaptationRejection,
    adapt_schemathesis_case,
)
from openapi_ai_test_evaluator.generation.schemathesis_capture import (
    capture_schemathesis_case,
)

SCHEMATHESIS_ADAPTER_VERSION = "schemathesis-case-v1"


@dataclass(frozen=True, slots=True)
class RejectedSchemathesisCase:
    """One input case that could not be represented faithfully by the runner."""

    case_id: str
    rejections: tuple[AdaptationRejection, ...]


@dataclass(frozen=True, slots=True)
class SchemathesisBatchAdaptation:
    """Separate executable cases from metrics about the adaptation boundary."""

    batch: TestCaseBatch | None
    record: AdaptationRecord
    rejected_cases: tuple[RejectedSchemathesisCase, ...]


class SchemathesisBatchCollector:
    """Receive generated cases one at a time without executing their HTTP requests."""

    def __init__(self, spec: OpenAPISpec, *, seed: int | None = None) -> None:
        self._spec = spec
        self._seed = seed
        self._received_count = 0
        self._adapted_cases: list[TestCase] = []
        self._rejected_cases: list[RejectedSchemathesisCase] = []

    def add(self, case: Case) -> None:
        """Capture and adapt one case, preserving any rejection for later metrics."""
        self._received_count += 1
        case_id = f"schemathesis-{self._received_count:04d}"
        capture = capture_schemathesis_case(case, self._spec, case_id=case_id)
        if capture.captured is None:
            self._reject(case_id, capture.rejections)
            return

        adaptation = adapt_schemathesis_case(capture.captured, self._spec)
        if adaptation.case is None:
            self._reject(case_id, adaptation.rejections)
            return
        self._adapted_cases.append(adaptation.case)

    def finish(self) -> SchemathesisBatchAdaptation:
        """Build immutable artifacts from all cases received so far."""
        reason_counts = Counter(
            (rejected.rejections[0].code.value, rejected.rejections[0].detail_code)
            for rejected in self._rejected_cases
        )
        skip_reasons = [
            AdaptationSkipReason(code=code, detail_code=detail_code, count=count)
            for (code, detail_code), count in sorted(
                reason_counts.items(), key=lambda item: (item[0][0], item[0][1] or "")
            )
        ]
        record = AdaptationRecord(
            schema_version="1.0",
            kind="AdaptationRecord",
            tool="schemathesis",
            tool_version=schemathesis.__version__,
            adapter_version=SCHEMATHESIS_ADAPTER_VERSION,
            seed=self._seed,
            received_case_count=self._received_count,
            adapted_case_count=len(self._adapted_cases),
            rejected_case_count=len(self._rejected_cases),
            skip_reasons=skip_reasons,
        )
        batch = (
            TestCaseBatch(schema_version="1.0", cases=list(self._adapted_cases))
            if self._adapted_cases
            else None
        )
        return SchemathesisBatchAdaptation(
            batch=batch,
            record=record,
            rejected_cases=tuple(self._rejected_cases),
        )

    def _reject(
        self,
        case_id: str,
        rejections: tuple[AdaptationRejection, ...],
    ) -> None:
        if not rejections:
            raise ValueError("rejected Schemathesis cases require at least one reason")
        self._rejected_cases.append(
            RejectedSchemathesisCase(case_id=case_id, rejections=rejections)
        )
