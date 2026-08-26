"""Strict contracts for deterministic response-side fault injection."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from openapi_ai_test_evaluator.domain.contracts import ContractModel, Identifier, JsonPointer
from openapi_ai_test_evaluator.domain.execution import HttpMethod

HttpStatus = Annotated[int, Field(ge=100, le=599)]
NonNegativeInt = Annotated[int, Field(ge=0)]
FAULT_ID_RESPONSE_HEADER = "x-oate-fault-id"


class FaultCategory(StrEnum):
    STATUS = "status"
    RESPONSE_BODY = "response_body"


class FaultMatcher(ContractModel):
    """Conditions that a forwarded response must satisfy before mutation."""

    method: HttpMethod
    path_regex: str = Field(min_length=1)
    response_statuses: list[HttpStatus] = Field(default_factory=list)
    response_media_type: str | None = Field(default=None, min_length=1)

    @field_validator("path_regex")
    @classmethod
    def validate_path_regex(cls, value: str) -> str:
        if not value.startswith("^/"):
            raise ValueError("path_regex must be anchored at the start of an absolute path")
        try:
            re.compile(value)
        except re.error as error:
            raise ValueError(f"path_regex is invalid: {error}") from error
        return value

    @field_validator("response_statuses")
    @classmethod
    def require_unique_statuses(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("response_statuses must not contain duplicates")
        return value


class ReplaceStatusMutation(ContractModel):
    type: Literal["replace_status"]
    status_code: HttpStatus


class RemoveJsonValueMutation(ContractModel):
    type: Literal["remove_json_value"]
    pointer: JsonPointer

    @field_validator("pointer")
    @classmethod
    def reject_root_pointer(cls, value: str) -> str:
        if value == "":
            raise ValueError("remove_json_value cannot remove the document root")
        return value


class ReplaceJsonValueMutation(ContractModel):
    type: Literal["replace_json_value"]
    pointer: JsonPointer
    value: JsonValue


class DuplicateJsonArrayItemMutation(ContractModel):
    type: Literal["duplicate_json_array_item"]
    pointer: JsonPointer
    index: NonNegativeInt


FaultMutation = Annotated[
    ReplaceStatusMutation
    | RemoveJsonValueMutation
    | ReplaceJsonValueMutation
    | DuplicateJsonArrayItemMutation,
    Field(discriminator="type"),
]


class FaultDefinition(ContractModel):
    """One deterministic response fault that can be enabled independently."""

    schema_version: Literal["1.0"]
    fault_id: Identifier
    description: str = Field(min_length=1)
    category: FaultCategory
    matcher: FaultMatcher
    mutation: FaultMutation

    @model_validator(mode="after")
    def validate_category(self) -> Self:
        expected_category = (
            FaultCategory.STATUS
            if isinstance(self.mutation, ReplaceStatusMutation)
            else FaultCategory.RESPONSE_BODY
        )
        if self.category is not expected_category:
            raise ValueError(
                f"fault category {self.category.value!r} does not match mutation "
                f"{self.mutation.type!r}"
            )
        return self


class FaultProxyMode(StrEnum):
    PASS_THROUGH = "pass_through"
    ACTIVE = "active"


class FaultProxyState(ContractModel):
    """Observable proxy state used to determine whether a fault really triggered."""

    mode: FaultProxyMode
    configured_fault_id: Identifier | None
    trigger_count: NonNegativeInt

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.mode is FaultProxyMode.PASS_THROUGH:
            if self.configured_fault_id is not None or self.trigger_count != 0:
                raise ValueError(
                    "pass_through mode requires no configured fault and a zero trigger count"
                )
        elif self.configured_fault_id is None:
            raise ValueError("active mode requires configured_fault_id")
        return self


__all__ = [
    "DuplicateJsonArrayItemMutation",
    "FaultCategory",
    "FaultDefinition",
    "FAULT_ID_RESPONSE_HEADER",
    "FaultMatcher",
    "FaultMutation",
    "FaultProxyMode",
    "FaultProxyState",
    "RemoveJsonValueMutation",
    "ReplaceJsonValueMutation",
    "ReplaceStatusMutation",
]
