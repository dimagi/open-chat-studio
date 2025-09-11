from dataclasses import dataclass
from typing import Self
from urllib.parse import parse_qs, urlparse

from django.conf import settings


@dataclass
class ColumnFilter:
    column: str
    operator: str
    value: str


class FilterParams:
    def __init__(self, query_params: dict):
        self._filters = {}

        for i in range(settings.MAX_FILTER_PARAMS):
            filter_column = query_params.get(f"filter_{i}_column")
            filter_operator = query_params.get(f"filter_{i}_operator")
            filter_value = query_params.get(f"filter_{i}_value")

            self._filters[filter_column] = ColumnFilter(
                column=filter_column, operator=filter_operator, value=filter_value
            )

    def get(self, column: str) -> ColumnFilter | None:
        return self._filters.get(column)

    @staticmethod
    def from_request(request) -> Self:
        query_params = request.GET
        if not query_params and (hx_url := request.headers.get("HX-Current-URL")):
            parsed_url = urlparse(hx_url)
            query_params = parse_qs(parsed_url.query)
        return FilterParams(query_params)


class DynamicFilter:
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
    def apply_filter(self, queryset, filter_params: FilterParams, timezone=None):
        column_filter = filter_params.get(self.query_param)
        if not column_filter:
            return queryset

        return self.apply(queryset, column_filter, timezone)

    def apply(self, queryset, column_filter: ColumnFilter, timezone=None):
        return queryset
