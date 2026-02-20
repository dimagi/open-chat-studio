from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel

from apps.help.base import BaseHelpAgent
from apps.help.registry import register_agent
from apps.web.dynamic_filters.datastructures import ColumnFilterData

_system_prompt = None


def _get_system_prompt():
    global _system_prompt
    if _system_prompt is None:
        _system_prompt = (Path(__file__).parent.parent / "filter_system_prompt.md").read_text()
    return _system_prompt


class FilterInput(BaseModel):
    query: str
    filter_slug: str


class FilterOutput(BaseModel):
    filters: list[ColumnFilterData]


@register_agent
class FilterAgent(BaseHelpAgent[FilterInput, FilterOutput]):
    name: ClassVar[str] = "filter"
    mode: ClassVar[Literal["high", "low"]] = "low"

    @classmethod
    def get_system_prompt(cls, input: FilterInput) -> str:
        from apps.web.dynamic_filters.base import get_filter_registry, get_filter_schema

        registry = get_filter_registry()
        filter_class = registry.get(input.filter_slug)
        if filter_class is None:
            raise ValueError(f"Unknown filter slug: {input.filter_slug!r}. Available: {sorted(registry.keys())}")
        schema = get_filter_schema(filter_class)
        template = _get_system_prompt()
        return template.format(schema=json.dumps(schema, indent=2))

    @classmethod
    def get_user_message(cls, input: FilterInput) -> str:
        return input.query
