from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from enum import StrEnum
from typing import Any, ClassVar, Literal

from django.db.models import QuerySet
from pydantic import BaseModel, Field, computed_field

from .datastructures import FilterParams

logger = logging.getLogger("ocs.filters")


class Operators(StrEnum):
    """Enum for filter operators used in dynamic filters."""

    EQUALS = "equals"
    CONTAINS = "contains"
    DOES_NOT_CONTAIN = "does not contain"
    STARTS_WITH = "starts with"
    ENDS_WITH = "ends with"
    ON = "on"
    BEFORE = "before"
    AFTER = "after"
    ANY_OF = "any of"
    ALL_OF = "all of"
    EXCLUDES = "excludes"
    RANGE = "range"


TYPE_STRING = "string"
TYPE_TIMESTAMP = "timestamp"
TYPE_CHOICE = "choice"
TYPE_EXCLUSIVE_CHOICE = "exclusive_choice"

TYPE_ANNOTATION = Literal[TYPE_STRING, TYPE_TIMESTAMP, TYPE_CHOICE, TYPE_EXCLUSIVE_CHOICE]

FIELD_TYPE_FILTERS = {
    TYPE_STRING: [
        Operators.EQUALS,
        Operators.CONTAINS,
        Operators.DOES_NOT_CONTAIN,
        Operators.STARTS_WITH,
        Operators.ENDS_WITH,
        Operators.ANY_OF,
    ],
    TYPE_TIMESTAMP: [Operators.ON, Operators.BEFORE, Operators.AFTER, Operators.RANGE],
    TYPE_CHOICE: [Operators.ANY_OF, Operators.ALL_OF, Operators.EXCLUDES],
    TYPE_EXCLUSIVE_CHOICE: [Operators.ANY_OF, Operators.EXCLUDES],
}

DATE_RANGE_OPTIONS = [
    {"label": "Last 1 Hour", "value": "1h"},
    {"label": "Last 1 Day", "value": "1d"},
    {"label": "Last 7 Days", "value": "7d"},
    {"label": "Last 15 Days", "value": "15d"},
    {"label": "Last 30 Days", "value": "30d"},
    {"label": "Last 3 Months", "value": "90d"},
    {"label": "Last Year", "value": "365d"},
]


class MultiColumnFilter:
    """
    A container for a list of ColumnFilter instances that can be applied to a queryset.

    This class is used to combine multiple filters into a single filter that can be applied to a queryset.
    It iterates through a list of `ColumnFilter`s and applies them to a queryset based on the
    filter parameters from the request.

    Attributes:
        filters: A list of `ColumnFilter` instances.
        date_range_column: The query_param of the default timestamp filter used for date range queries.
    """

    slug: ClassVar[str] = ""
    filters: ClassVar[Sequence[ColumnFilter]]
    date_range_column: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if col := cls.__dict__.get("date_range_column", ""):
            query_params = {f.query_param for f in cls.__dict__.get("filters", [])}
            if query_params and col not in query_params:
                raise ValueError(
                    f"{cls.__name__}.date_range_column={col!r} does not match "
                    f"any filter query_param. Available: {sorted(query_params)}"
                )

    @classmethod
    def columns(cls, team, **kwargs) -> dict[str, dict]:
        # Create per-call copies to avoid mutating shared instances
        instances = [f.model_copy(deep=True) for f in cls.filters]
        for filter_component in instances:
            filter_component.prepare(team, **kwargs)
        return {filter_component.query_param: filter_component.model_dump() for filter_component in instances}

    def prepare_queryset(self, queryset):
        """Hook for subclasses to modify the queryset before applying filters."""
        return queryset

    def apply(self, queryset: QuerySet, filter_params: FilterParams, timezone=None) -> QuerySet:
        """Applies the filters to the given queryset based on the `self.filter_params`."""
        queryset = self.prepare_queryset(queryset)

        for filter_component in self.filters:
            queryset = filter_component.apply(queryset, filter_params, timezone)

        return queryset.distinct()


class ColumnFilter(BaseModel):
    """
    Abstract base class for a single column filter.

    Each `ColumnFilter` implementation is responsible for applying a specific filter to a queryset.
    It bridges the gap between URL query parameters and ORM filters. When a request contains a
    `filter_{i}_column` parameter that matches the `query_param` of a `ColumnFilter`, this filter
    processes the associated operator and value to generate the appropriate database query.

    Attributes:
        query_param: The name of the query parameter used in the URL to identify this filter.
    """

    query_param: str
    label: str
    type: TYPE_ANNOTATION
    column: str = None
    description: str = ""

    @computed_field
    @property
    def operators(self) -> list[Operators]:
        return FIELD_TYPE_FILTERS[self.type]

    def prepare(self, team, **kwargs):
        pass

    def values_list(self, json_value: str) -> list[str]:
        try:
            return json.loads(json_value)
        except json.JSONDecodeError:
            logger.exception("Failed to decode JSON for filter value: %s: %s", self.query_param, json_value)
        return []

    def parse_query_value(self, query_value) -> any:
        """Parses the query value from the URL into a format suitable for filtering."""
        return query_value

    def apply(self, queryset: QuerySet, filter_params: FilterParams, timezone=None) -> QuerySet:
        column_filter = filter_params.get(self.query_param)
        if not column_filter:
            return queryset

        operator = column_filter.operator.replace(" ", "_").lower()
        if method := getattr(self, f"apply_{operator}", None):
            parsed_value = self.parse_query_value(column_filter.value)
            if parsed_value not in (None, "", []):
                return method(queryset, parsed_value, timezone)
        return queryset


class ChoiceColumnFilter(ColumnFilter):
    type: str = TYPE_EXCLUSIVE_CHOICE
    options: list[str | dict[str, Any]] = Field(default_factory=list)

    def parse_query_value(self, query_value) -> any:
        return self.values_list(query_value)

    def apply_any_of(self, queryset, value, timezone=None) -> QuerySet:
        return queryset.filter(**{f"{self.column}__in": value})

    def apply_all_of(self, queryset, value, timezone=None) -> QuerySet:
        for val in value:
            queryset = queryset.filter(**{f"{self.column}": val})
        return queryset

    def apply_excludes(self, queryset, value, timezone=None) -> QuerySet:
        return queryset.exclude(**{f"{self.column}__in": value})


class StringColumnFilter(ColumnFilter):
    type: str = TYPE_STRING
    columns: list[str] = Field(default_factory=list)

    def _apply_with_lookup(self, queryset, lookup, value):
        """Apply filter with optional OR logic across multiple columns.
        queryset: The queryset to filter
        lookup: The Django lookup to use (e.g., 'icontains', 'istartswith'), or None for exact match
        value: The value to filter by
        """
        from django.db.models import Q

        # Build Q object for OR logic
        q = Q()

        for col in self.columns:
            filter_key = f"{col}__{lookup}" if lookup else col
            q |= Q(**{filter_key: value})

        return queryset.filter(q)

    def apply_equals(self, queryset, value, timezone=None) -> QuerySet:
        return self._apply_with_lookup(queryset, None, value)

    def apply_contains(self, queryset, value, timezone=None) -> QuerySet:
        return self._apply_with_lookup(queryset, "icontains", value)

    def apply_does_not_contain(self, queryset, value, timezone=None) -> QuerySet:
        from django.db.models import Q

        # For exclusion: exclude if it matches ANY column
        q = Q()
        for col in self.columns:
            q |= Q(**{f"{col}__icontains": value})

        return queryset.exclude(q)

    def apply_starts_with(self, queryset, value, timezone=None) -> QuerySet:
        return self._apply_with_lookup(queryset, "istartswith", value)

    def apply_ends_with(self, queryset, value, timezone=None) -> QuerySet:
        return self._apply_with_lookup(queryset, "iendswith", value)

    def apply_any_of(self, queryset, value, timezone=None) -> QuerySet:
        if values := self.values_list(value):
            from django.db.models import Q

            # OR logic across multiple columns
            q = Q()
            for col in self.columns:
                q |= Q(**{f"{col}__in": values})
            return queryset.filter(q)

        return queryset


def get_filter_schema(filter_class: type[MultiColumnFilter]) -> dict[str, dict]:
    """Extract static schema from a MultiColumnFilter for use in AI prompts.

    Returns a dict keyed by query_param with label, type, description, and operators.
    Does not call prepare() -- no DB access needed.
    """
    schema = {}
    for f in filter_class.filters:
        schema[f.query_param] = {
            "label": f.label,
            "type": f.type,
            "description": f.description,
            "operators": [op.value for op in FIELD_TYPE_FILTERS[f.type]],
        }
    return schema


def get_filter_registry() -> dict[str, type[MultiColumnFilter]]:
    """Build registry of slug -> MultiColumnFilter class from direct subclasses."""
    return {cls.slug: cls for cls in MultiColumnFilter.__subclasses__() if getattr(cls, "slug", "")}
