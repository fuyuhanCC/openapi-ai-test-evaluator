from datetime import UTC, datetime
from pathlib import Path

import pytest

from openapi_ai_test_evaluator.domain.composition import SuiteCompositionRecord
from openapi_ai_test_evaluator.domain.generation import (
    AdaptationRecord,
    CaseAdmissionSummary,
    GenerationRecord,
    GenerationStatus,
)
from openapi_ai_test_evaluator.evaluation import (
    CompositionRecordLoadError,
    SourceRecordLoadError,
    load_composition_record,
    load_source_record,
)


@pytest.mark.parametrize(
    "record",
    [
        AdaptationRecord(
            schema_version="1.0",
            kind="AdaptationRecord",
            tool="schemathesis",
            tool_version="4.25.2",
            adapter_version="schemathesis-case-v1",
            seed=7,
            received_case_count=1,
            adapted_case_count=1,
            rejected_case_count=0,
            skip_reasons=[],
        ),
        GenerationRecord(
            schema_version="1.0",
            kind="GenerationRecord",
            generation_id="deepseek-r1",
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="api-cases-v3",
            started_at=datetime(2026, 8, 27, tzinfo=UTC),
            finished_at=datetime(2026, 8, 27, tzinfo=UTC),
            duration_ms=0,
            status=GenerationStatus.SUCCEEDED,
            request_count=1,
            case_admission=CaseAdmissionSummary(
                received_case_count=1,
                admitted_case_count=1,
                rejected_case_count=0,
                rejections=[],
            ),
            error=None,
        ),
    ],
)
def test_loads_both_supported_source_record_kinds(
    tmp_path: Path,
    record: AdaptationRecord | GenerationRecord,
) -> None:
    path = tmp_path / "record.json"
    path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")

    loaded = load_source_record(path)

    assert loaded == record


def test_rejects_unknown_source_record_kind(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_text('{"kind":"UnknownRecord"}\n', encoding="utf-8")

    with pytest.raises(SourceRecordLoadError, match="invalid source record"):
        load_source_record(path)


def test_loads_a_composition_record(tmp_path: Path) -> None:
    path = tmp_path / "composition.json"
    record = SuiteCompositionRecord.model_validate(
        {
            "schema_version": "1.0",
            "kind": "SuiteCompositionRecord",
            "composition_id": "deepseek-enhanced-r1",
            "base_batch": {"case_count": 2, "sha256": "0" * 64},
            "enhancements": [
                {
                    "pack_id": "shared-relations-v1",
                    "batch": {"case_count": 1, "sha256": "1" * 64},
                }
            ],
            "composed_batch": {"case_count": 3, "sha256": "2" * 64},
        }
    )
    path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")

    loaded = load_composition_record(path)

    assert loaded.composition_id == "deepseek-enhanced-r1"


def test_rejects_invalid_composition_record(tmp_path: Path) -> None:
    path = tmp_path / "composition.json"
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(CompositionRecordLoadError, match="invalid composition record"):
        load_composition_record(path)
