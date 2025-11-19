"""Field definitions for evaluator output schemas.

Defines typed field definitions with validation constraints for different data types.
Uses discriminated unions to ensure type-specific constraints are enforced.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class BaseFieldDefinition(BaseModel):
    """Base class for field definitions with common functionality."""

    description: str

    @property
    def python_type(self) -> type:
        """Get the corresponding Python type. Must be implemented by subclasses."""
        raise NotImplementedError

    @property
    def pydantic_fields(self) -> dict:
        return self.model_dump(exclude={"type"}, exclude_none=True)


class StringFieldDefinition(BaseFieldDefinition):
    """String field with validation constraints."""

    type: Literal["string"]
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None

    @property
    def python_type(self) -> type:
        return str


class IntFieldDefinition(BaseFieldDefinition):
    """Integer field with validation constraints."""

    type: Literal["int"]
    ge: int | None = None
    le: int | None = None
    gt: int | None = None
    lt: int | None = None

    @property
    def python_type(self) -> type:
        return int


class FloatFieldDefinition(BaseFieldDefinition):
    """Float field with validation constraints."""

    type: Literal["float"]
    ge: float | None = None
    le: float | None = None
    gt: float | None = None
    lt: float | None = None

    @property
    def python_type(self) -> type:
        return float


class ChoiceFieldDefinition(BaseFieldDefinition):
    type: Literal["choice"]
    choices: list[str]

    @property
    def python_type(self) -> type:
        members = {c.upper(): c for c in self.choices}
        return StrEnum("DynamicEnum", members)


FieldDefinition = StringFieldDefinition | IntFieldDefinition | FloatFieldDefinition | ChoiceFieldDefinition
