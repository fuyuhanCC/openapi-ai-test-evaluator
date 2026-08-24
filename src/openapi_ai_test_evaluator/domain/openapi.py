"""Normalized OpenAPI contracts used by semantic validation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from openapi_ai_test_evaluator.domain.contracts import ContractModel

SchemaDefinition = dict[str, Any] | bool
SUPPORTED_STRING_FORMATS = frozenset({"date", "date-time", "uuid", "email", "uri", "ipv4", "ipv6"})


class ParameterLocation(StrEnum):
    PATH = "path"
    QUERY = "query"
    HEADER = "header"


class ParameterModel(ContractModel):
    name: str
    location: ParameterLocation
    required: bool = False
    schema_definition: SchemaDefinition = Field(default_factory=dict)
    description: str | None = None


class RequestBodyModel(ContractModel):
    required: bool = False
    schema_definition: SchemaDefinition | None = None
    description: str | None = None


class ResponseModel(ContractModel):
    status_code: str
    schema_definition: SchemaDefinition | None = None
    description: str | None = None


class OperationModel(ContractModel):
    operation_id: str
    method: str
    path: str
    parameters: list[ParameterModel] = Field(default_factory=list)
    request_body: RequestBodyModel | None = None
    responses: dict[str, ResponseModel] = Field(default_factory=dict)
    unsupported_reasons: list[str] = Field(default_factory=list)
    summary: str | None = None
    description: str | None = None

    def parameter(self, location: ParameterLocation, name: str) -> ParameterModel | None:
        """Return a parameter using case-insensitive matching for headers."""
        for parameter in self.parameters:
            names_match = (
                parameter.name.casefold() == name.casefold()
                if location is ParameterLocation.HEADER
                else parameter.name == name
            )
            if parameter.location is location and names_match:
                return parameter
        return None


class OpenAPISpec(ContractModel):
    spec_id: str
    openapi_version: str
    title: str
    version: str
    operations: dict[str, OperationModel]
    document: dict[str, Any] = Field(exclude=True)
    description: str | None = None
