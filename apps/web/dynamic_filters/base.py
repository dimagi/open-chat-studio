from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from .datastructures import ColumnFilterData, FilterParams


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
    filters: list[ColumnFilter] = []

    def __init__(self, filter_params: FilterParams):
        self.filter_params = filter_params

    @classmethod
    def columns(cls) -> list[str]:
        return [filter_component.query_param for filter_component in cls.filters]

    def prepare_queryset(self, queryset):
        return queryset

    def apply(self, queryset, timezone):
        """Apply all filters to the queryset."""
        queryset = self.prepare_queryset(queryset)

        for filter_component in self.filters:
            if column_filter := self.filter_params.get(filter_component.query_param):
                queryset = filter_component.apply(queryset, column_filter, timezone)

        return queryset.distinct()


class ColumnFilter(ABC):
    query_param: str = None

    @abstractmethod
    def apply(self, queryset, column_filter: ColumnFilterData, timezone=None):
        pass
