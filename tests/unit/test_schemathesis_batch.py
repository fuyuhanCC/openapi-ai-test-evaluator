from pathlib import Path
from types import SimpleNamespace

from schemathesis.core import NOT_SET

from openapi_ai_test_evaluator.generation import SchemathesisBatchCollector
from openapi_ai_test_evaluator.spec import load_openapi

ROOT = Path(__file__).parents[2]
SPEC = load_openapi(ROOT / "examples" / "demo-items" / "openapi.yaml")


def fake_case(**changes: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "operation": SimpleNamespace(
            definition=SimpleNamespace(raw={"operationId": "listItems"}),
            method="get",
            path="/items",
        ),
        "method": "GET",
        "path": "/items",
        "path_parameters": {},
        "query": {"limit": 20},
        "headers": {},
        "cookies": {},
        "body": NOT_SET,
        "media_type": None,
        "meta": SimpleNamespace(
            generation=SimpleNamespace(mode=SimpleNamespace(value="positive")),
            phase=SimpleNamespace(name=SimpleNamespace(value="coverage")),
        ),
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_collects_adapted_cases_and_records_rejections() -> None:
    collector = SchemathesisBatchCollector(SPEC, seed=7)
    collector.add(fake_case())  # type: ignore[arg-type]
    collector.add(fake_case(query=[]))  # type: ignore[arg-type]

    result = collector.finish()

    assert result.batch is not None
    assert [case.id for case in result.batch.cases] == ["schemathesis-0001"]
    assert result.record.seed == 7
    assert result.record.received_case_count == 2
    assert result.record.adapted_case_count == 1
    assert result.record.rejected_case_count == 1
    assert [reason.model_dump() for reason in result.record.skip_reasons] == [
        {
            "code": "capture_component_unsupported",
            "detail_code": "query",
            "count": 1,
        }
    ]
    assert result.rejected_cases[0].case_id == "schemathesis-0002"


def test_returns_no_batch_when_every_case_is_rejected() -> None:
    collector = SchemathesisBatchCollector(SPEC)
    collector.add(fake_case(query=[]))  # type: ignore[arg-type]
    collector.add(fake_case(query=[]))  # type: ignore[arg-type]

    result = collector.finish()

    assert result.batch is None
    assert result.record.adapted_case_count == 0
    assert result.record.rejected_case_count == 2
    assert result.record.skip_reasons[0].count == 2


def test_empty_collection_produces_an_explicit_zero_count_record() -> None:
    result = SchemathesisBatchCollector(SPEC, seed=11).finish()

    assert result.batch is None
    assert result.rejected_cases == ()
    assert result.record.received_case_count == 0
    assert result.record.skip_reasons == []
