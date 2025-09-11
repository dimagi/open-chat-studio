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
    def __init__(self, filter_params: FilterParams):
        self.filter_params = filter_params

    def prepare_queryset(self, queryset):
        return queryset

    def apply(self, queryset, timezone):
        """Apply all filters to the queryset."""
        queryset = self.prepare_queryset(queryset)

        for filter_component in self.filters:
            queryset = filter_component.apply_filter(queryset, self.filter_params, timezone)

        return queryset.distinct()


class ColumnFilterMixin:
    query_param: str = None

    def apply_filter(self, queryset, filter_params: FilterParams, timezone=None):
        column_filter = filter_params.get(self.query_param)
        if not column_filter:
            return queryset

        return self.apply(queryset, column_filter, timezone)

    def apply(self, queryset, column_filter: ColumnFilterData, timezone=None):
        return queryset
