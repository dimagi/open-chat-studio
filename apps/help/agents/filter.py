from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel

from apps.help.base import BaseHelpAgent
from apps.help.registry import register_agent
from apps.web.dynamic_filters.datastructures import ColumnFilterData


class FilterInput(BaseModel):
    query: str


class FilterOutput(BaseModel):
    filters: list[ColumnFilterData]


@register_agent
class FilterAgent(BaseHelpAgent[FilterInput, FilterOutput]):
    name: ClassVar[str] = "filter"
    mode: ClassVar[Literal["high", "low"]] = "low"

    @classmethod
    def get_system_prompt(cls, input: FilterInput) -> str:
        return "TODO: Implement filter agent system prompt"

    @classmethod
    def get_user_message(cls, input: FilterInput) -> str:
        return input.query
