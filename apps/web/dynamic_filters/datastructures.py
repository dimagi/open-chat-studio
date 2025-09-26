from typing import Self
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.http import QueryDict
from pydantic import BaseModel


class ColumnFilterData(BaseModel):
    """Data class representing a single column's filter data."""

    column: str
    operator: str
    value: str

    def __bool__(self):
        return bool(self.column and self.operator and self.value)


class FilterParams:
    """A container for filter parameters extracted from a request's query parameters."""

    def __init__(self, query_params: QueryDict | None = None, column_filters: list[ColumnFilterData] | None = None):
        self.filters: dict[str, ColumnFilterData] = {}

        if query_params:
            for i in range(settings.MAX_FILTER_PARAMS):
                filter_column = query_params.get(f"filter_{i}_column")
                filter_operator = query_params.get(f"filter_{i}_operator")
                filter_value = query_params.get(f"filter_{i}_value")
                if filter_column and filter_operator and filter_value:
                    self.filters[filter_column] = ColumnFilterData(
                        column=filter_column, operator=filter_operator, value=filter_value
                    )

        if column_filters:
            for item in column_filters:
                self.filters[item.column] = item

    @staticmethod
    def from_request(request) -> Self:
        query_params = request.GET
        if not query_params and (hx_url := request.headers.get("HX-Current-URL")):
            parsed_url = urlparse(hx_url)
            query_params = QueryDict(parsed_url.query)
        return FilterParams(query_params)

    def get(self, column: str) -> ColumnFilterData | None:
        return self.filters.get(column)

    def to_query(self) -> str:
        query_data = {}
        for i, filter_data in enumerate(self.filters.values()):
            query_data.update(
                {
                    f"filter_{i}_column": filter_data.column,
                    f"filter_{i}_operator": filter_data.operator,
                    f"filter_{i}_value": filter_data.value,
                }
            )
        return urlencode(query_data)
