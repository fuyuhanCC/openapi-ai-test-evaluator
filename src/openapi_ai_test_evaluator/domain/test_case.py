"""Provider-independent, runner-ready API test case contracts."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import (
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from openapi_ai_test_evaluator.domain.contracts import (
    ContractModel,
    Identifier,
    JsonPointer,
)
from openapi_ai_test_evaluator.domain.runtime_values import validate_runtime_value


class RequestMode(StrEnum):
    CONFORMANT = "conformant"
    INTENTIONALLY_INVALID = "intentionally_invalid"


class ViolationCode(StrEnum):
    MISSING_REQUIRED = "missing_required"
    TYPE_MISMATCH = "type_mismatch"
    SCHEMA_MISMATCH = "schema_mismatch"
    INVALID_ENUM = "invalid_enum"
    OUT_OF_RANGE = "out_of_range"
    INVALID_FORMAT = "invalid_format"
    ADDITIONAL_PROPERTY = "additional_property"


class AssertionOperator(StrEnum):
    STATUS_IS = "status_is"
    STATUS_IN = "status_in"
    SCHEMA_MATCHES = "schema_matches"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    EXISTS = "exists"
    CONTAINS = "contains"
    LENGTH_IS = "length_is"
    ITEMS_UNIQUE_BY = "items_unique_by"
    GREATER_THAN = "greater_than"
    MATCHES_PATTERN = "matches_pattern"


class CleanupWhen(StrEnum):
    ALWAYS = "always"
    ON_SUCCESS = "on_success"
    ON_FAILURE = "on_failure"


class RelationKind(StrEnum):
    METAMORPHIC = "metamorphic"
    LIFECYCLE = "lifecycle"


class RelationType(StrEnum):
    REPEATED_READ = "repeated_read_consistency"
    QUERY_ORDER = "query_parameter_order_invariance"
    PAGINATION = "pagination_monotonicity"
    CREATE_READ = "create_read_consistency"
    UPDATE_READ = "update_read_consistency"
    DELETE_READ = "delete_read_consistency"

    @property
    def kind(self) -> RelationKind:
        if self in METAMORPHIC_RELATION_TYPES:
            return RelationKind.METAMORPHIC
        if self in LIFECYCLE_RELATION_TYPES:
            return RelationKind.LIFECYCLE
        raise ValueError(f"relation type {self.value!r} has no explicit kind")


METAMORPHIC_RELATION_TYPES = frozenset(
    {
        RelationType.REPEATED_READ,
        RelationType.QUERY_ORDER,
        RelationType.PAGINATION,
    }
)
LIFECYCLE_RELATION_TYPES = frozenset(
    {
        RelationType.CREATE_READ,
        RelationType.UPDATE_READ,
        RelationType.DELETE_READ,
    }
)


class PaginationMode(StrEnum):
    SUBSET = "subset"
    PREFIX = "prefix"


class ExpectedViolation(ContractModel):
    code: ViolationCode
    location: Literal["body", "path", "query", "header"]
    field: str = Field(min_length=1)


class QueryParameter(ContractModel):
    name: str = Field(min_length=1)
    value: JsonValue

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: JsonValue) -> JsonValue:
        return validate_runtime_value(value)


class RequestDefinition(ContractModel):
    mode: RequestMode = RequestMode.CONFORMANT
    expected_violations: list[ExpectedViolation] = Field(default_factory=list)
    path: dict[str, JsonValue] = Field(default_factory=dict)
    query: list[QueryParameter] = Field(default_factory=list)
    headers: dict[str, JsonValue] = Field(default_factory=dict)
    body: JsonValue | None = None

    @property
    def body_present(self) -> bool:
        """Whether the input explicitly included a JSON body, including null."""
        return "body" in self.model_fields_set

    @model_serializer(mode="wrap")
    def preserve_body_presence(
        self,
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, Any]:
        serialized = handler(self)
        if not self.body_present:
            serialized.pop("body", None)
        return serialized

    @field_validator("path", "headers")
    @classmethod
    def validate_parameter_values(cls, values: dict[str, JsonValue]) -> dict[str, JsonValue]:
        for value in values.values():
            validate_runtime_value(value)
        return values

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: JsonValue | None) -> JsonValue | None:
        if value is not None:
            validate_runtime_value(value)
        return value

    @model_validator(mode="after")
    def validate_negative_intent(self) -> Self:
        if self.mode is RequestMode.CONFORMANT and self.expected_violations:
            raise ValueError("conformant requests cannot declare expected_violations")
        if self.mode is RequestMode.INTENTIONALLY_INVALID and not self.expected_violations:
            raise ValueError("intentionally_invalid requests must declare expected_violations")
        return self


class ResponseSelector(ContractModel):
    source: Literal["response.body", "response.headers", "response.status"]
    pointer: JsonPointer | None = None

    @model_validator(mode="after")
    def validate_pointer(self) -> Self:
        if self.source in {"response.body", "response.headers"} and self.pointer is None:
            raise ValueError(f"{self.source} selectors require a JSON pointer")
        if self.source == "response.status" and self.pointer is not None:
            raise ValueError("response.status selectors cannot have a JSON pointer")
        return self


class Extraction(ContractModel):
    variable: str = Field(min_length=1)
    source: Literal["response.body", "response.headers"]
    pointer: JsonPointer
    required: bool = True


class Assertion(ContractModel):
    id: Identifier | None = None
    operator: AssertionOperator
    actual: ResponseSelector | None = None
    expected: JsonValue | None = None

    @field_validator("expected")
    @classmethod
    def validate_expected(cls, value: JsonValue | None) -> JsonValue | None:
        if value is not None:
            validate_runtime_value(value)
        return value

    @model_validator(mode="after")
    def validate_operator_arguments(self) -> Self:
        expected_provided = "expected" in self.model_fields_set
        if self.operator is AssertionOperator.STATUS_IS:
            if self.actual is not None:
                raise ValueError("status_is does not accept an actual selector")
            if (
                not expected_provided
                or not isinstance(self.expected, int)
                or isinstance(self.expected, bool)
            ):
                raise ValueError("status_is requires an integer expected value")
        elif self.operator is AssertionOperator.STATUS_IN:
            if self.actual is not None:
                raise ValueError("status_in does not accept an actual selector")
            valid_statuses = (
                isinstance(self.expected, list)
                and bool(self.expected)
                and all(
                    isinstance(status, int) and not isinstance(status, bool)
                    for status in self.expected
                )
            )
            if not valid_statuses:
                raise ValueError("status_in requires a non-empty list of integer status codes")
            if len(self.expected) != len(set(self.expected)):
                raise ValueError("status_in status codes must be unique")
        elif self.operator is AssertionOperator.SCHEMA_MATCHES:
            if self.actual is not None or self.expected is not None:
                raise ValueError("schema_matches does not accept actual or expected")
        elif self.operator is AssertionOperator.EXISTS:
            if self.actual is None:
                raise ValueError("exists requires an actual selector")
            if self.expected is not None:
                raise ValueError("exists does not accept an expected value")
        elif self.operator is AssertionOperator.ITEMS_UNIQUE_BY:
            if self.actual is None or self.actual.source != "response.body":
                raise ValueError("items_unique_by requires a response.body selector")
            valid_pointer = (
                expected_provided
                and isinstance(self.expected, str)
                and (self.expected == "" or self.expected.startswith("/"))
                and re.search(r"~(?![01])", self.expected) is None
            )
            if not valid_pointer:
                raise ValueError("items_unique_by requires a JSON Pointer expected value")
        else:
            if self.actual is None or not expected_provided:
                raise ValueError(f"{self.operator.value} requires actual and expected")
            expected_is_reference = isinstance(self.expected, dict) and set(self.expected) == {
                "$var"
            }
            if self.operator is AssertionOperator.LENGTH_IS and not expected_is_reference:
                if (
                    not isinstance(self.expected, int)
                    or isinstance(self.expected, bool)
                    or self.expected < 0
                ):
                    raise ValueError("length_is requires a non-negative integer expected value")
            if self.operator is AssertionOperator.GREATER_THAN and not expected_is_reference:
                if not isinstance(self.expected, (int, float)) or isinstance(self.expected, bool):
                    raise ValueError("greater_than requires a numeric expected value")
            if self.operator is AssertionOperator.MATCHES_PATTERN and not expected_is_reference:
                if not isinstance(self.expected, str):
                    raise ValueError("matches_pattern requires a string expected value")
                try:
                    re.compile(self.expected)
                except re.error as error:
                    raise ValueError(
                        "matches_pattern requires a valid regular expression"
                    ) from error
        return self


class RequestStep(ContractModel):
    id: Identifier
    operation_id: str = Field(min_length=1)
    request: RequestDefinition = Field(default_factory=RequestDefinition)
    extract: list[Extraction] = Field(default_factory=list)
    assertions: list[Assertion] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_local_ids(self) -> Self:
        assertion_ids = [assertion.id for assertion in self.assertions if assertion.id is not None]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("assertion IDs must be unique within a step")
        extraction_names = [extraction.variable for extraction in self.extract]
        if len(extraction_names) != len(set(extraction_names)):
            raise ValueError("extracted variable names must be unique within a step")
        return self


class CleanupStep(RequestStep):
    when: CleanupWhen = CleanupWhen.ALWAYS
    ignore_errors: bool = False


class RelationFieldReference(ContractModel):
    location: Literal["request.body", "response.body", "response.status"]
    pointer: JsonPointer | None = None

    @model_validator(mode="after")
    def validate_pointer(self) -> Self:
        if self.location.endswith(".body") and self.pointer is None:
            raise ValueError("body relation references require a JSON pointer")
        return self


class RelationFieldPair(ContractModel):
    source: RelationFieldReference
    follow_up: RelationFieldReference


class ScenarioRelation(ContractModel):
    id: Identifier
    type: RelationType
    source_step: Identifier
    follow_up_step: Identifier
    compare_pointers: list[JsonPointer] = Field(default_factory=list)
    ignore_pointers: list[JsonPointer] = Field(default_factory=list)
    collection_pointer: JsonPointer | None = None
    item_key_pointer: JsonPointer | None = None
    mode: PaginationMode | None = None
    page_size_parameter: str | None = Field(default=None, min_length=1)
    field_pairs: list[RelationFieldPair] = Field(default_factory=list)
    baseline_step: Identifier | None = None
    stable_follow_up_pointers: list[JsonPointer] = Field(default_factory=list)
    accepted_follow_up_statuses: list[int] = Field(default_factory=list)

    @property
    def kind(self) -> RelationKind:
        return self.type.kind

    @model_validator(mode="after")
    def validate_relation_arguments(self) -> Self:
        if self.type is RelationType.REPEATED_READ and not self.compare_pointers:
            raise ValueError("repeated_read_consistency requires compare_pointers")
        if self.type in {RelationType.QUERY_ORDER, RelationType.PAGINATION}:
            if self.collection_pointer is None or self.item_key_pointer is None:
                raise ValueError(f"{self.type.value} requires collection and item key pointers")
        if self.type is RelationType.PAGINATION:
            if self.mode is None:
                raise ValueError("pagination_monotonicity requires a comparison mode")
            if self.page_size_parameter is None:
                raise ValueError("pagination_monotonicity requires a page_size_parameter")
        if self.type in {RelationType.CREATE_READ, RelationType.UPDATE_READ}:
            if not self.field_pairs:
                raise ValueError(f"{self.type.value} requires field_pairs")
        if (
            self.type is RelationType.UPDATE_READ
            and self.stable_follow_up_pointers
            and self.baseline_step is None
        ):
            raise ValueError("update_read_consistency requires baseline_step for stable fields")
        if self.type is RelationType.DELETE_READ and not self.accepted_follow_up_statuses:
            raise ValueError("delete_read_consistency requires accepted follow-up statuses")
        return self


class TestCase(ContractModel):
    """One independently executable API test with one or more request steps."""

    id: Identifier
    name: str | None = None
    tags: list[str] = Field(default_factory=list)
    setup: list[RequestStep] = Field(default_factory=list)
    steps: list[RequestStep] = Field(min_length=1)
    cleanup: list[CleanupStep] = Field(default_factory=list)
    relations: list[ScenarioRelation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_step_and_relation_ids(self) -> Self:
        executable_steps = [*self.setup, *self.steps]
        all_steps = [*executable_steps, *self.cleanup]
        step_ids = [step.id for step in all_steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step IDs must be unique within a test case")

        executable_ids = {step.id for step in executable_steps}
        relation_ids = [relation.id for relation in self.relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("relation IDs must be unique within a test case")
        for relation in self.relations:
            referenced_ids = {relation.source_step, relation.follow_up_step}
            if relation.baseline_step is not None:
                referenced_ids.add(relation.baseline_step)
            missing = referenced_ids - executable_ids
            if missing:
                missing_list = ", ".join(sorted(missing))
                raise ValueError(f"relation {relation.id} references unknown steps: {missing_list}")
        return self


class TestCaseBatch(ContractModel):
    """The versioned batch returned by one test-case generation attempt."""

    schema_version: Literal["1.0"]
    cases: list[TestCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_case_ids(self) -> Self:
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case IDs must be unique within a batch")
        return self


class ExecutionConfig(ContractModel):
    """Runtime settings kept separate from generated test-case content."""

    timeout_ms: int = Field(default=5000, ge=1, le=120_000)
    headers: dict[str, str] = Field(default_factory=dict)
    initial_variables: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("initial_variables")
    @classmethod
    def validate_initial_variables(
        cls,
        variables: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        for name, value in variables.items():
            if not name.strip():
                raise ValueError("variable names cannot be empty")
            validate_runtime_value(value)
        return variables
