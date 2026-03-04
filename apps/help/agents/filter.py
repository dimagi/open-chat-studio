from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel

from apps.help.agent import build_system_agent
from apps.help.base import BaseHelpAgent
from apps.help.registry import register_agent
from apps.teams.models import Team
from apps.web.dynamic_filters.base import ChoiceColumnFilter
from apps.web.dynamic_filters.datastructures import ColumnFilterData


@functools.cache
def _get_system_prompt():
    return (Path(__file__).parent.parent / "filter_system_prompt.md").read_text()


def make_get_options_tool(filter_class, team):
    """Return a LangChain tool that fetches options for a choice filter parameter.

    The tool is closed over filter_class and team so it can call prepare(team)
    on the appropriate ColumnFilter instance without needing extra arguments.
    """
    from langchain_core.tools import tool  # lazy-loaded to keep Django startup fast

    _options_cache: dict[str, list[dict]] = {}  # param -> normalized options (cached per agent run)

    @tool
    def get_filter_options(param: str, search: str = "") -> dict:
        """Get available options for a choice or exclusive_choice filter parameter.

        Call this tool before using a choice/exclusive_choice filter to look up
        valid option values. Use the returned option IDs (not labels) as filter values.

        Args:
            param: The filter query_param name (e.g. 'experiment', 'tags', 'channels').
            search: Optional case-insensitive substring to filter options by label.

        Returns:
            Dict with 'options' (list of {id, label}), 'returned' (count returned),
            'total' (total matching before limit is applied).
            On error, returns {'error': '<message>'}.
        """
        filter_component = next(
            (f for f in filter_class.filters if f.query_param == param),
            None,
        )
        if filter_component is None:
            return {"error": f"No filter with param {param!r} found"}
        if not isinstance(filter_component, ChoiceColumnFilter):
            return {"error": f"Filter {param!r} does not have options (type: {filter_component.type})"}

        if param not in _options_cache:
            try:
                instance = filter_component.model_copy(deep=True)
                instance.prepare(team)

                normalized = []
                for opt in instance.options:
                    if isinstance(opt, str):
                        normalized.append({"id": opt, "label": opt})
                    elif isinstance(opt, dict) and "label" in opt:
                        normalized.append({"id": opt.get("id", opt["label"]), "label": str(opt["label"])})

                _options_cache[param] = normalized
            except Exception as exc:
                return {"error": f"Failed to resolve options for {param!r}: {exc}"}

        normalized = _options_cache[param]

        if search:
            needle = search.lower()
            normalized = [opt for opt in normalized if needle in opt["label"].lower()]
        if not normalized:
            # return all if nothing found
            normalized = _options_cache[param]

        limit = 50
        total = len(normalized)
        limited = normalized[:limit]
        return {"options": limited, "returned": len(limited), "total": total}

    return get_filter_options


class FilterInput(BaseModel):
    query: str
    filter_slug: str
    team_id: int


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
        date_range_column = filter_class.date_range_column
        template = _get_system_prompt()
        return template.format(schema=json.dumps(schema, indent=2), date_range_column=date_range_column)

    @classmethod
    def get_user_message(cls, input: FilterInput) -> str:
        return input.query

    def run(self) -> FilterOutput:
        from apps.web.dynamic_filters.base import get_filter_registry

        registry = get_filter_registry()
        filter_class = registry.get(self.input.filter_slug)
        if filter_class is None:
            raise ValueError(f"Unknown filter slug: {self.input.filter_slug!r}. Available: {sorted(registry.keys())}")
        team = Team.objects.get(id=self.input.team_id)
        options_tool = make_get_options_tool(filter_class, team)
        agent = build_system_agent(
            self.mode,
            self.get_system_prompt(self.input),
            tools=[options_tool],
            response_format=self._get_output_type(),
        )
        response = agent.invoke({"messages": [{"role": "user", "content": self.get_user_message(self.input)}]})
        return self.parse_response(response)
