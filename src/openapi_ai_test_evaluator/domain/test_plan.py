"""Legacy TestPlan wrapper retained while callers migrate to TestCaseBatch."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from openapi_ai_test_evaluator.domain.contracts import (
    ContractModel,
    Identifier,
    JsonPointer,
)
from openapi_ai_test_evaluator.domain.runtime_values import validate_runtime_value
from openapi_ai_test_evaluator.domain.test_case import (
    LIFECYCLE_RELATION_TYPES,
    METAMORPHIC_RELATION_TYPES,
    Assertion,
    AssertionOperator,
    CleanupStep,
    CleanupWhen,
    ExpectedViolation,
    Extraction,
    PaginationMode,
    QueryParameter,
    RelationFieldPair,
    RelationFieldReference,
    RelationKind,
    RelationType,
    RequestDefinition,
    RequestMode,
    RequestStep,
    ResponseSelector,
    ScenarioRelation,
    TestCase,
    ViolationCode,
)


class GeneratorType(StrEnum):
    MANUAL = "manual"
    RULE = "rule"
    DEEPSEEK = "deepseek"
    METAMORPHIC = "metamorphic"


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


class Scenario(TestCase):
    """Legacy name for a TestCase nested inside TestPlan."""


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
            validate_runtime_value(value)
        return variables

    @model_validator(mode="after")
    def validate_unique_scenarios(self) -> Self:
        scenario_ids = [scenario.id for scenario in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("scenario IDs must be unique within a plan")
        return self


__all__ = [
    "LIFECYCLE_RELATION_TYPES",
    "METAMORPHIC_RELATION_TYPES",
    "Assertion",
    "AssertionOperator",
    "CleanupStep",
    "CleanupWhen",
    "ContractModel",
    "ExpectedViolation",
    "Extraction",
    "GeneratorMetadata",
    "GeneratorType",
    "Identifier",
    "JsonPointer",
    "PaginationMode",
    "PlanDefaults",
    "PlanMetadata",
    "QueryParameter",
    "RelationFieldPair",
    "RelationFieldReference",
    "RelationKind",
    "RelationType",
    "RequestDefinition",
    "RequestMode",
    "RequestStep",
    "ResponseSelector",
    "Scenario",
    "ScenarioRelation",
    "Target",
    "TestPlan",
    "ViolationCode",
]
