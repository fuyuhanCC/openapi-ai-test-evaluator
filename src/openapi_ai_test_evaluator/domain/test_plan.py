"""Strict, declarative contracts for generated API test plans."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$")]
JsonPointer = Annotated[str, Field(pattern=r"^(|/.*)$")]


class ContractModel(BaseModel):
    """Base model for contracts that must reject unknown input."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GeneratorType(StrEnum):
    MANUAL = "manual"
    RULE = "rule"
    DEEPSEEK = "deepseek"
    METAMORPHIC = "metamorphic"


class RequestMode(StrEnum):
    CONFORMANT = "conformant"
    INTENTIONALLY_INVALID = "intentionally_invalid"


class ViolationCode(StrEnum):
    MISSING_REQUIRED = "missing_required"
    TYPE_MISMATCH = "type_mismatch"
    INVALID_ENUM = "invalid_enum"
    OUT_OF_RANGE = "out_of_range"
    INVALID_FORMAT = "invalid_format"
    ADDITIONAL_PROPERTY = "additional_property"


class AssertionOperator(StrEnum):
    STATUS_IS = "status_is"
    SCHEMA_MATCHES = "schema_matches"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    EXISTS = "exists"
    CONTAINS = "contains"
    LENGTH_IS = "length_is"
    GREATER_THAN = "greater_than"
    MATCHES_PATTERN = "matches_pattern"


class CleanupWhen(StrEnum):
    ALWAYS = "always"
    ON_SUCCESS = "on_success"
    ON_FAILURE = "on_failure"


class RelationType(StrEnum):
    REPEATED_READ = "repeated_read_consistency"
    QUERY_ORDER = "query_parameter_order_invariance"
    PAGINATION = "pagination_monotonicity"
    CREATE_READ = "create_read_consistency"
    UPDATE_READ = "update_read_consistency"
    DELETE_READ = "delete_read_consistency"


class PaginationMode(StrEnum):
    SUBSET = "subset"
    PREFIX = "prefix"


def _validate_runtime_value(value: JsonValue) -> JsonValue:
    """Validate reserved variable references inside otherwise ordinary JSON."""
    if isinstance(value, dict):
        if "$var" in value:
            if set(value) != {"$var"}:
                raise ValueError("a $var reference cannot contain sibling keys")
            variable_name = value["$var"]
            if not isinstance(variable_name, str) or not variable_name.strip():
                raise ValueError("$var must contain a non-empty variable name")
            return value
        for nested in value.values():
            _validate_runtime_value(nested)
    elif isinstance(value, list):
        for nested in value:
            _validate_runtime_value(nested)
    return value


class GeneratorMetadata(ContractModel):
    type: GeneratorType
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None


class PlanMetadata(ContractModel):
    name: Identifier
    description: str | None = None
    generator: GeneratorMetadata


class Target(ContractModel):
    spec_id: str = Field(min_length=1)


class PlanDefaults(ContractModel):
    timeout_ms: int = Field(default=5000, ge=1, le=120_000)
    headers: dict[str, str] = Field(default_factory=dict)


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
        return _validate_runtime_value(value)


class RequestDefinition(ContractModel):
    mode: RequestMode = RequestMode.CONFORMANT
    expected_violations: list[ExpectedViolation] = Field(default_factory=list)
    path: dict[str, JsonValue] = Field(default_factory=dict)
    query: list[QueryParameter] = Field(default_factory=list)
    headers: dict[str, JsonValue] = Field(default_factory=dict)
    body: JsonValue | None = None

    @field_validator("path", "headers")
    @classmethod
    def validate_parameter_values(cls, values: dict[str, JsonValue]) -> dict[str, JsonValue]:
        for value in values.values():
            _validate_runtime_value(value)
        return values

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: JsonValue | None) -> JsonValue | None:
        if value is not None:
            _validate_runtime_value(value)
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
    def require_pointer_for_body(self) -> Self:
        if self.source == "response.body" and self.pointer is None:
            raise ValueError("response.body selectors require a JSON pointer")
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
            _validate_runtime_value(value)
        return value

    @model_validator(mode="after")
    def validate_operator_arguments(self) -> Self:
        if self.operator is AssertionOperator.STATUS_IS:
            if not isinstance(self.expected, int) or isinstance(self.expected, bool):
                raise ValueError("status_is requires an integer expected value")
        elif self.operator is AssertionOperator.SCHEMA_MATCHES:
            if self.actual is not None or self.expected is not None:
                raise ValueError("schema_matches does not accept actual or expected")
        elif self.operator is AssertionOperator.EXISTS:
            if self.actual is None:
                raise ValueError("exists requires an actual selector")
        elif self.actual is None or self.expected is None:
            raise ValueError(f"{self.operator.value} requires actual and expected")
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


class MetamorphicRelation(ContractModel):
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


class Scenario(ContractModel):
    id: Identifier
    name: str | None = None
    tags: list[str] = Field(default_factory=list)
    setup: list[RequestStep] = Field(default_factory=list)
    steps: list[RequestStep] = Field(min_length=1)
    cleanup: list[CleanupStep] = Field(default_factory=list)
    relations: list[MetamorphicRelation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_step_and_relation_ids(self) -> Self:
        executable_steps = [*self.setup, *self.steps]
        all_steps = [*executable_steps, *self.cleanup]
        step_ids = [step.id for step in all_steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step IDs must be unique within a scenario")

        executable_ids = {step.id for step in executable_steps}
        relation_ids = [relation.id for relation in self.relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("relation IDs must be unique within a scenario")
        for relation in self.relations:
            referenced_ids = {relation.source_step, relation.follow_up_step}
            if relation.baseline_step is not None:
                referenced_ids.add(relation.baseline_step)
            missing = referenced_ids - executable_ids
            if missing:
                missing_list = ", ".join(sorted(missing))
                raise ValueError(f"relation {relation.id} references unknown steps: {missing_list}")
        return self


class TestPlan(ContractModel):
    schema_version: Literal["1.0"]
    kind: Literal["TestPlan"]
    metadata: PlanMetadata
    target: Target
    defaults: PlanDefaults = Field(default_factory=PlanDefaults)
    variables: dict[str, JsonValue] = Field(default_factory=dict)
    scenarios: list[Scenario] = Field(min_length=1)

    @field_validator("variables")
    @classmethod
    def validate_variables(cls, variables: dict[str, JsonValue]) -> dict[str, JsonValue]:
        for name, value in variables.items():
            if not name.strip():
                raise ValueError("variable names cannot be empty")
            _validate_runtime_value(value)
        return variables

    @model_validator(mode="after")
    def validate_unique_scenarios(self) -> Self:
        scenario_ids = [scenario.id for scenario in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("scenario IDs must be unique within a plan")
        return self
