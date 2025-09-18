from dataclasses import dataclass
from typing import Self
from urllib.parse import urlparse

from django.conf import settings
from django.http import QueryDict


@dataclass
class ColumnFilterData:
    """Data class representing a single column's filter data."""

    column: str
    operator: str
    value: str

    def __bool__(self):
        return bool(self.column and self.operator and self.value)

    def as_query_string(self, filter_number=0) -> str:
        return "filter_{fn}_column={col}&filter_{fn}_operator={op}&filter_{fn}_value={val}".format(  # noqa: UP032
            col=self.column, op=self.operator, val=self.value, fn=filter_number
        )


class FilterParams:
    """A container for filter parameters extracted from a request's query parameters."""

    def __init__(self, query_params: QueryDict):
        self._filters = {}

        for i in range(settings.MAX_FILTER_PARAMS):
            filter_column = query_params.get(f"filter_{i}_column")
            filter_operator = query_params.get(f"filter_{i}_operator")
            filter_value = query_params.get(f"filter_{i}_value")
            if filter_column and filter_operator and filter_value:
                self._filters[filter_column] = ColumnFilterData(
                    column=filter_column, operator=filter_operator, value=filter_value
                )

    def get(self, column: str) -> ColumnFilterData | None:
        return self._filters.get(column)

    @staticmethod
    def from_request(request) -> Self:
        query_params = request.GET
        if not query_params and (hx_url := request.headers.get("HX-Current-URL")):
            parsed_url = urlparse(hx_url)
            query_params = QueryDict(parsed_url.query)
        return FilterParams(query_params)
