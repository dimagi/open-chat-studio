from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import StrEnum
from typing import ClassVar

from django.db.models import QuerySet

from .datastructures import ColumnFilterData, FilterParams

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

    def values_list(self, column_filter: ColumnFilterData) -> list[str]:
        try:
            return json.loads(column_filter.value)
        except json.JSONDecodeError:
            logger.error("Failed to decode JSON for chat message tag filter", exc_info=True)

    def apply(self, queryset: QuerySet, filter_params: FilterParams, timezone=None) -> QuerySet:
        if column_filter := filter_params.get(self.query_param):
            return self.apply_filter(queryset, column_filter, timezone)
        return queryset

    @abstractmethod
    def apply_filter(self, queryset: QuerySet, column_filter: ColumnFilterData, timezone=None) -> QuerySet:
        """Applies the filter to the given queryset based on the `column_filter` data."""
        pass
