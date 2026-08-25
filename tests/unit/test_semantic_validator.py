from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from openapi_ai_test_evaluator.domain import TestPlan as PlanModel
from openapi_ai_test_evaluator.domain.test_plan import RelationKind, RequestStep
from openapi_ai_test_evaluator.spec import load_openapi
from openapi_ai_test_evaluator.validation import (
    detect_request_violations,
    load_test_plan,
    validate_plan_semantics,
)

ROOT = Path(__file__).parents[2]
PLAN_DIR = ROOT / "examples" / "plans"
SPEC_PATH_30 = ROOT / "examples" / "demo-items" / "openapi.yaml"
SPEC_PATH_31 = ROOT / "examples" / "demo-items" / "openapi-3.1.yaml"
DEMO_SPEC_PATHS = [
    pytest.param(SPEC_PATH_30, id="openapi-3.0"),
    pytest.param(SPEC_PATH_31, id="openapi-3.1"),
]
SPEC = load_openapi(SPEC_PATH_30)


@pytest.mark.parametrize("spec_path", DEMO_SPEC_PATHS)
@pytest.mark.parametrize(
    "plan_path",
    sorted(PLAN_DIR.glob("*.yaml")),
    ids=lambda path: path.stem,
)
def test_reviewed_plans_match_demo_openapi(plan_path: Path, spec_path: Path) -> None:
    plan = load_test_plan(plan_path)
    spec = load_openapi(spec_path)

    assert validate_plan_semantics(plan, spec) == []


def _minimal_plan_data() -> dict[str, object]:
    return deepcopy(load_test_plan(PLAN_DIR / "minimal-get.yaml").model_dump(mode="json"))


def _plan_data_with_relation(relation_type: str) -> dict[str, object]:
    lifecycle_types = {
        "create_read_consistency",
        "update_read_consistency",
        "delete_read_consistency",
    }
    filename = "lifecycle.yaml" if relation_type in lifecycle_types else "metamorphic.yaml"
    return deepcopy(load_test_plan(PLAN_DIR / filename).model_dump(mode="json"))


def _first_step(plan_data: dict[str, object]) -> dict[str, object]:
    scenarios = plan_data["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[0]
    assert isinstance(scenario, dict)
    steps = scenario["steps"]
    assert isinstance(steps, list)
    step = steps[0]
    assert isinstance(step, dict)
    return step


def _scenario_with_relation(plan_data: dict[str, object], relation_type: str) -> dict[str, object]:
    scenarios = plan_data["scenarios"]
    assert isinstance(scenarios, list)
    for scenario in scenarios:
        assert isinstance(scenario, dict)
        relations = scenario["relations"]
        assert isinstance(relations, list)
        if any(relation["type"] == relation_type for relation in relations):
            return scenario
    raise AssertionError(f"missing relation type {relation_type}")


def _relation(scenario: dict[str, object]) -> dict[str, object]:
    relations = scenario["relations"]
    assert isinstance(relations, list)
    relation = relations[0]
    assert isinstance(relation, dict)
    return relation


def _step(scenario: dict[str, object], step_id: str) -> dict[str, object]:
    for section in ("setup", "steps"):
        steps = scenario[section]
        assert isinstance(steps, list)
        for step in steps:
            assert isinstance(step, dict)
            if step["id"] == step_id:
                return step
    raise AssertionError(f"missing step {step_id}")


def test_relation_examples_are_separated_by_kind() -> None:
    metamorphic_plan = load_test_plan(PLAN_DIR / "metamorphic.yaml")
    lifecycle_plan = load_test_plan(PLAN_DIR / "lifecycle.yaml")
    metamorphic_relations = [
        relation for scenario in metamorphic_plan.scenarios for relation in scenario.relations
    ]
    lifecycle_relations = [
        relation for scenario in lifecycle_plan.scenarios for relation in scenario.relations
    ]

    assert len(metamorphic_relations) == 3
    assert {relation.kind for relation in metamorphic_relations} == {RelationKind.METAMORPHIC}
    assert len(lifecycle_relations) == 3
    assert {relation.kind for relation in lifecycle_relations} == {RelationKind.LIFECYCLE}


def test_reports_unknown_operation() -> None:
    plan_data = _minimal_plan_data()
    _first_step(plan_data)["operation_id"] = "missingOperation"

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert [issue.code for issue in issues] == ["unknown_operation"]


def test_reports_unknown_query_parameter() -> None:
    plan_data = _minimal_plan_data()
    request = _first_step(plan_data)["request"]
    assert isinstance(request, dict)
    request["query"] = [{"name": "surprise", "value": "yes"}]

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert "unknown_parameter" in {issue.code for issue in issues}


def test_reports_undeclared_response_status() -> None:
    plan_data = _minimal_plan_data()
    assertions = _first_step(plan_data)["assertions"]
    assert isinstance(assertions, list)
    assertions[0]["expected"] = 418

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert "undeclared_response_status" in {issue.code for issue in issues}


def test_reports_undeclared_response_status_inside_status_set() -> None:
    plan_data = _minimal_plan_data()
    assertions = _first_step(plan_data)["assertions"]
    assert isinstance(assertions, list)
    assertions[0] = {"operator": "status_in", "expected": [200, 418]}

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    undeclared = [issue for issue in issues if issue.code == "undeclared_response_status"]
    assert len(undeclared) == 1
    assert "418" in undeclared[0].message


def test_reports_conformant_request_schema_violation() -> None:
    plan_data = deepcopy(load_test_plan(PLAN_DIR / "all-methods.yaml").model_dump(mode="json"))
    create_step = _first_step(plan_data)
    request = create_step["request"]
    assert isinstance(request, dict)
    body = request["body"]
    assert isinstance(body, dict)
    del body["price"]

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert any(
        issue.code == "request_schema_violation" and "body.price" in issue.message
        for issue in issues
    )


def test_reports_spec_id_mismatch() -> None:
    plan_data = _minimal_plan_data()
    target = plan_data["target"]
    assert isinstance(target, dict)
    target["spec_id"] = "another-api"

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert issues[0].code == "spec_id_mismatch"


def test_reports_unknown_runtime_variable() -> None:
    plan_data = deepcopy(load_test_plan(PLAN_DIR / "all-methods.yaml").model_dump(mode="json"))
    scenarios = plan_data["scenarios"]
    assert isinstance(scenarios, list)
    steps = scenarios[0]["steps"]
    steps[1]["request"]["path"]["itemId"] = {"$var": "never_defined"}

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert "unknown_variable" in {issue.code for issue in issues}


def test_reports_unknown_runtime_variable_in_assertion_expected_value() -> None:
    plan_data = _minimal_plan_data()
    assertions = _first_step(plan_data)["assertions"]
    assert isinstance(assertions, list)
    assertions.append(
        {
            "operator": "equals",
            "actual": {"source": "response.body", "pointer": "/id"},
            "expected": {"$var": "never_defined"},
        }
    )

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert "unknown_variable" in {issue.code for issue in issues}


def test_rejects_composite_http_parameter_value() -> None:
    plan_data = _minimal_plan_data()
    request = _first_step(plan_data)["request"]
    assert isinstance(request, dict)
    request["query"] = [{"name": "status", "value": ["active", "inactive"]}]

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert "unsupported_parameter_serialization" in {issue.code for issue in issues}


def test_rejects_plan_variable_that_resolves_to_composite_http_parameter() -> None:
    plan_data = _minimal_plan_data()
    variables = plan_data["variables"]
    assert isinstance(variables, dict)
    variables["statuses"] = ["active", "inactive"]
    request = _first_step(plan_data)["request"]
    assert isinstance(request, dict)
    request["query"] = [{"name": "status", "value": {"$var": "statuses"}}]

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert "unsupported_parameter_serialization" in {issue.code for issue in issues}


def test_rejects_composite_undeclared_transport_header() -> None:
    plan_data = _minimal_plan_data()
    request = _first_step(plan_data)["request"]
    assert isinstance(request, dict)
    request["headers"] = {"X-Metadata": {"source": "test"}}

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert "unsupported_parameter_serialization" in {issue.code for issue in issues}


def test_reports_false_and_undeclared_negative_intent() -> None:
    plan_data = deepcopy(load_test_plan(PLAN_DIR / "negative.yaml").model_dump(mode="json"))
    scenarios = plan_data["scenarios"]
    assert isinstance(scenarios, list)
    request = scenarios[0]["steps"][0]["request"]
    request["expected_violations"][0]["field"] = "price"

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)
    codes = {issue.code for issue in issues}

    assert "false_expected_violation" in codes
    assert "undeclared_request_violation" in codes


def test_detects_expected_violations_for_a_concrete_negative_request() -> None:
    step = RequestStep.model_validate(
        {
            "id": "generated-create",
            "operation_id": "createItem",
            "request": {
                "body": {
                    "name": "",
                    "price": "free",
                }
            },
        }
    )

    report = detect_request_violations(step, SPEC)

    assert report.issues == ()
    assert [
        (violation.code.value, violation.location, violation.field)
        for violation in report.violations
    ] == [
        ("missing_required", "body", "status"),
        ("out_of_range", "body", "name"),
        ("type_mismatch", "body", "price"),
    ]


def test_request_violation_detection_reports_non_inferable_input() -> None:
    step = RequestStep.model_validate(
        {
            "id": "generated-list",
            "operation_id": "listItems",
            "request": {"query": [{"name": "surprise", "value": "yes"}]},
        }
    )

    report = detect_request_violations(step, SPEC)

    assert report.violations == ()
    assert [issue.code for issue in report.issues] == ["unknown_parameter"]
    assert report.issues[0].path == "step.request.query.surprise"


def test_request_violation_detection_distinguishes_missing_and_null_body() -> None:
    missing = RequestStep(id="missing-body", operation_id="createItem")
    explicit_null = RequestStep.model_validate(
        {
            "id": "null-body",
            "operation_id": "createItem",
            "request": {"body": None},
        }
    )

    missing_report = detect_request_violations(missing, SPEC)
    null_report = detect_request_violations(explicit_null, SPEC)

    assert [violation.model_dump(mode="json") for violation in missing_report.violations] == [
        {"code": "missing_required", "location": "body", "field": "$body"}
    ]
    assert [violation.model_dump(mode="json") for violation in null_report.violations] == [
        {"code": "type_mismatch", "location": "body", "field": "$body"}
    ]


def test_request_violation_detection_reports_unknown_operation() -> None:
    report = detect_request_violations(
        RequestStep(id="unknown", operation_id="missingOperation"),
        SPEC,
    )

    assert report.violations == ()
    assert [issue.code for issue in report.issues] == ["unknown_operation"]


def test_reports_missing_required_body_and_unexpected_body() -> None:
    create_data = deepcopy(load_test_plan(PLAN_DIR / "all-methods.yaml").model_dump(mode="json"))
    create_request = _first_step(create_data)["request"]
    assert isinstance(create_request, dict)
    create_request["body"] = None

    read_data = _minimal_plan_data()
    read_request = _first_step(read_data)["request"]
    assert isinstance(read_request, dict)
    read_request["body"] = {"unexpected": True}

    create_issues = validate_plan_semantics(PlanModel.model_validate(create_data), SPEC)
    read_issues = validate_plan_semantics(PlanModel.model_validate(read_data), SPEC)

    assert "request_schema_violation" in {issue.code for issue in create_issues}
    assert "unexpected_request_body" in {issue.code for issue in read_issues}


def test_distinguishes_missing_required_body_from_explicit_json_null() -> None:
    missing_data = deepcopy(
        load_test_plan(PLAN_DIR / "all-methods.yaml").model_dump(mode="json")
    )
    missing_request = _first_step(missing_data)["request"]
    assert isinstance(missing_request, dict)
    del missing_request["body"]

    null_data = deepcopy(load_test_plan(PLAN_DIR / "all-methods.yaml").model_dump(mode="json"))
    null_request = _first_step(null_data)["request"]
    assert isinstance(null_request, dict)
    null_request["body"] = None

    missing_issues = validate_plan_semantics(PlanModel.model_validate(missing_data), SPEC)
    null_issues = validate_plan_semantics(PlanModel.model_validate(null_data), SPEC)

    assert any("required request body is missing" in issue.message for issue in missing_issues)
    assert not any("required request body is missing" in issue.message for issue in null_issues)
    assert "request_schema_violation" in {issue.code for issue in null_issues}


def test_query_order_relation_rejects_different_parameter_values() -> None:
    plan_data = _plan_data_with_relation("query_parameter_order_invariance")
    scenario = _scenario_with_relation(plan_data, "query_parameter_order_invariance")
    steps = scenario["steps"]
    assert isinstance(steps, list)
    steps[1]["request"]["query"][0]["value"] = "different-category"

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert "relation_query_mismatch" in {issue.code for issue in issues}


def test_query_order_relation_requires_order_to_change() -> None:
    plan_data = _plan_data_with_relation("query_parameter_order_invariance")
    scenario = _scenario_with_relation(plan_data, "query_parameter_order_invariance")
    steps = scenario["steps"]
    assert isinstance(steps, list)
    steps[1]["request"]["query"] = deepcopy(steps[0]["request"]["query"])

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert "relation_query_order_unchanged" in {issue.code for issue in issues}


def test_pagination_relation_requires_explicit_page_size_parameter() -> None:
    plan_data = _plan_data_with_relation("pagination_monotonicity")
    scenario = _scenario_with_relation(plan_data, "pagination_monotonicity")
    del _relation(scenario)["page_size_parameter"]

    with pytest.raises(ValidationError, match="requires a page_size_parameter"):
        PlanModel.model_validate(plan_data)


def test_update_stable_fields_require_a_baseline_step() -> None:
    plan_data = _plan_data_with_relation("update_read_consistency")
    scenario = _scenario_with_relation(plan_data, "update_read_consistency")
    _relation(scenario)["baseline_step"] = None

    with pytest.raises(ValidationError, match="requires baseline_step"):
        PlanModel.model_validate(plan_data)


def test_repeated_read_requires_equivalent_requests() -> None:
    plan_data = _plan_data_with_relation("repeated_read_consistency")
    scenario = _scenario_with_relation(plan_data, "repeated_read_consistency")
    second_read = _step(scenario, "second-read")
    second_read["request"]["path"]["itemId"] = 999

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert "relation_request_mismatch" in {issue.code for issue in issues}


def test_pagination_relation_requires_fixed_context_and_increasing_size() -> None:
    plan_data = _plan_data_with_relation("pagination_monotonicity")
    scenario = _scenario_with_relation(plan_data, "pagination_monotonicity")
    large_page = _step(scenario, "large-page")
    large_page["request"]["query"][0]["value"] = 1
    large_page["request"]["query"][1]["value"] = 4

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)
    codes = {issue.code for issue in issues}

    assert "relation_pagination_context_mismatch" in codes
    assert "relation_pagination_size_invalid" in codes


def test_create_read_requires_extracted_resource_link() -> None:
    plan_data = _plan_data_with_relation("create_read_consistency")
    scenario = _scenario_with_relation(plan_data, "create_read_consistency")
    read = _step(scenario, "read-created")
    read["request"]["path"]["itemId"] = 1

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert "relation_resource_not_linked" in {issue.code for issue in issues}


def test_update_read_requires_same_resource_and_compatible_fields() -> None:
    plan_data = _plan_data_with_relation("update_read_consistency")
    scenario = _scenario_with_relation(plan_data, "update_read_consistency")
    read = _step(scenario, "read-updated")
    read["request"]["path"]["itemId"] = 999
    pair = _relation(scenario)["field_pairs"][0]
    pair["follow_up"]["pointer"] = "/price"

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)
    codes = {issue.code for issue in issues}

    assert "relation_resource_not_linked" in codes
    assert "relation_field_type_mismatch" in codes


def test_relation_type_compatibility_supports_openapi_31_type_arrays() -> None:
    plan_data = _plan_data_with_relation("create_read_consistency")
    scenario = _scenario_with_relation(plan_data, "create_read_consistency")
    pair = _relation(scenario)["field_pairs"][0]
    pair["source"]["pointer"] = "/category"
    pair["follow_up"]["pointer"] = "/name"

    issues = validate_plan_semantics(
        PlanModel.model_validate(plan_data),
        load_openapi(SPEC_PATH_31),
    )

    assert "relation_field_type_mismatch" not in {issue.code for issue in issues}


def test_delete_read_requires_delete_method_and_success_assertion() -> None:
    plan_data = _plan_data_with_relation("delete_read_consistency")
    scenario = _scenario_with_relation(plan_data, "delete_read_consistency")
    delete = _step(scenario, "delete")
    delete["operation_id"] = "updateItem"
    delete["assertions"] = []

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)
    codes = {issue.code for issue in issues}

    assert "relation_method_mismatch" in codes
    assert "relation_delete_success_unasserted" in codes


def test_relation_steps_must_follow_execution_order() -> None:
    plan_data = _plan_data_with_relation("repeated_read_consistency")
    scenario = _scenario_with_relation(plan_data, "repeated_read_consistency")
    relation = _relation(scenario)
    relation["source_step"], relation["follow_up_step"] = (
        relation["follow_up_step"],
        relation["source_step"],
    )

    issues = validate_plan_semantics(PlanModel.model_validate(plan_data), SPEC)

    assert "relation_step_order_invalid" in {issue.code for issue in issues}
