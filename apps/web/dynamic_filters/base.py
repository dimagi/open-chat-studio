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
    """
    A container for a list of ColumnFilter instances that can be applied to a queryset.

    This class is used to combine multiple filters into a single filter that can be applied to a queryset.
    It iterates through a list of `ColumnFilter`s and applies them to a queryset based on the
    filter parameters from the request.

    Attributes:
        filters: A list of `ColumnFilter` instances.
    """

    filters: list[ColumnFilter] = []

    def __init__(self, filter_params: FilterParams):
        self.filter_params = filter_params

    @classmethod
    def columns(cls) -> list[str]:
        return [filter_component.query_param for filter_component in cls.filters]

    def prepare_queryset(self, queryset):
        """Hook for subclasses to modify the queryset before applying filters."""
        return queryset

    def apply(self, queryset, timezone):
        """Applies the filters to the given queryset based on the `self.filter_params`."""
        queryset = self.prepare_queryset(queryset)

        for filter_component in self.filters:
            if column_filter := self.filter_params.get(filter_component.query_param):
                queryset = filter_component.apply(queryset, column_filter, timezone)

        return queryset.distinct()


class ColumnFilter(ABC):
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

    @abstractmethod
    def apply(self, queryset, column_filter: ColumnFilterData, timezone=None):
        """Applies the filter to the given queryset based on the `column_filter` data."""
        pass
