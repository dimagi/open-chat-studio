import json
from typing import Self
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.http import QueryDict
from pydantic import BaseModel, model_validator

_LIST_OPERATORS = frozenset({"any of", "all of", "excludes"})


class ColumnFilterData(BaseModel):
    """Data class representing a single column's filter data."""

    column: str
    operator: str
    value: str

    @model_validator(mode="after")
    def _normalize_list_value(self) -> Self:
        """Wrap bare strings in a JSON array for operators that expect lists."""
        if self.operator in _LIST_OPERATORS:
            try:
                json.loads(self.value)
            except (json.JSONDecodeError, TypeError):
                self.value = json.dumps([self.value])
        return self

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

    @classmethod
    def from_request(cls, request) -> Self:
        query_params = request.GET
        if not any(key.startswith("filter_") for key in query_params):
            return cls.from_request_header(request, "HX-Current-URL")
        return cls(query_params)

    @classmethod
    def from_request_header(cls, request, header: str):
        if header_value := request.headers.get(header):
            parsed_url = urlparse(header_value)
            return cls(QueryDict(parsed_url.query))
        return cls()

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
