from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from enum import StrEnum
from typing import ClassVar

from django.db.models import QuerySet

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


FIELD_TYPE_FILTERS = {
    "string": [
        Operators.EQUALS,
        Operators.CONTAINS,
        Operators.DOES_NOT_CONTAIN,
        Operators.STARTS_WITH,
        Operators.ENDS_WITH,
        Operators.ANY_OF,
    ],
    "timestamp": [Operators.ON, Operators.BEFORE, Operators.AFTER, Operators.RANGE],
    "choice": [Operators.ANY_OF, Operators.ALL_OF, Operators.EXCLUDES],
}

DATE_RANGE_OPTIONS = [
    {"label": "Last 1 Hour", "value": "1h"},
    {"label": "Last 1 Day", "value": "1d"},
    {"label": "Last 7 Days", "value": "7d"},
    {"label": "Last 15 Days", "value": "15d"},
    {"label": "Last 30 Days", "value": "30d"},
]


class MultiColumnFilter:
    """
    A container for a list of ColumnFilter instances that can be applied to a queryset.

    This class is used to combine multiple filters into a single filter that can be applied to a queryset.
    It iterates through a list of `ColumnFilter`s and applies them to a queryset based on the
    filter parameters from the request.

    Attributes:
        filters: A list of `ColumnFilter` instances.
    """

    filters: ClassVar[Sequence[ColumnFilter]]

    @classmethod
    def columns(cls) -> list[str]:
        return [filter_component.query_param for filter_component in cls.filters]

    def prepare_queryset(self, queryset):
        """Hook for subclasses to modify the queryset before applying filters."""
        return queryset

    def apply(self, queryset: QuerySet, filter_params: FilterParams, timezone=None) -> QuerySet:
        """Applies the filters to the given queryset based on the `self.filter_params`."""
        queryset = self.prepare_queryset(queryset)

        for filter_component in self.filters:
            queryset = filter_component.apply(queryset, filter_params, timezone)

        return queryset.distinct()


class ColumnFilter:
    """
    Abstract base class for a single column filter.

    Each `ColumnFilter` implementation is responsible for applying a specific filter to a queryset.
    It bridges the gap between URL query parameters and ORM filters. When a request contains a
    `filter_{i}_column` parameter that matches the `query_param` of a `ColumnFilter`, this filter
    processes the associated operator and value to generate the appropriate database query.

    Attributes:
        query_param: The name of the query parameter used in the URL to identify this filter.
    """

    query_param: str = None

    def values_list(self, json_value: str) -> list[str]:
        try:
            return json.loads(json_value)
        except json.JSONDecodeError:
            logger.error("Failed to decode JSON for chat message tag filter", exc_info=True)

    def parse_query_value(self, query_value) -> any:
        """Parses the query value from the URL into a format suitable for filtering."""
        return query_value

    def apply(self, queryset: QuerySet, filter_params: FilterParams, timezone=None) -> QuerySet:
        column_filter = filter_params.get(self.query_param)
        if not column_filter:
            return queryset

        operator = column_filter.operator.replace(" ", "_").lower()
        if method := getattr(self, f"apply_{operator}", None):
            if parsed_value := self.parse_query_value(column_filter.value):
                return method(queryset, parsed_value, timezone)
        return queryset


class ChoiceFilterMixin:
    column: ClassVar[str]

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


class StringFilterMixin:
    column: ClassVar[str]

    def apply_equals(self, queryset, value, timezone=None) -> QuerySet:
        return queryset.filter(**{f"{self.column}": value})

    def apply_contains(self, queryset, value, timezone=None) -> QuerySet:
        return queryset.filter(**{f"{self.column}__icontains": value})

    def apply_does_not_contain(self, queryset, value, timezone=None) -> QuerySet:
        return queryset.exclude(**{f"{self.column}__icontains": value})

    def apply_starts_with(self, queryset, value, timezone=None) -> QuerySet:
        return queryset.filter(**{f"{self.column}__istartswith": value})

    def apply_ends_with(self, queryset, value, timezone=None) -> QuerySet:
        return queryset.filter(**{f"{self.column}__iendswith": value})

    def apply_any_of(self, queryset, value, timezone=None) -> QuerySet:
        if values := self.values_list(value):
            return queryset.filter(**{f"{self.column}__in": values})
        return queryset
