"""Shared strict contract primitives."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$")]
JsonPointer = Annotated[str, Field(pattern=r"^(|/.*)$")]


class ContractModel(BaseModel):
    """Base model for contracts that must reject unknown input."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
