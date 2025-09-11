from dataclasses import dataclass
from typing import Self
from urllib.parse import parse_qs, urlparse

from django.conf import settings


@dataclass
class ColumnFilterData:
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
            query_params = parse_qs(parsed_url.query)
        return FilterParams(query_params)
