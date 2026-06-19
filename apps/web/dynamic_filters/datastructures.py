import csv
import json
from io import StringIO
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
        """Wrap bare strings or tilde-separated values in a JSON array for operators that expect lists."""
        if self.operator in _LIST_OPERATORS:
            if isinstance(self.value, str):
                try:
                    parsed = json.loads(self.value)
                except (json.JSONDecodeError, TypeError):
                    parsed = None

                if isinstance(parsed, list):
                    self.value = json.dumps(parsed)
                elif "~" in self.value:
                    # Use CSV reader to handle special characters in values
                    reader = csv.reader(StringIO(self.value), delimiter="~")
                    try:
                        row = next(reader)
                        self.value = json.dumps(row)
                    except StopIteration:
                        self.value = json.dumps([self.value])
                else:
                    self.value = json.dumps([self.value])
            else:
                self.value = json.dumps([str(self.value)])
        return self

    def __bool__(self):
        return bool(self.column and self.operator and self.value)


class FilterParams:
    """A container for filter parameters extracted from a request's query parameters."""

    def __init__(self, query_params: QueryDict | None = None, column_filters: list[ColumnFilterData] | None = None):
        self.filters: dict[str, ColumnFilterData] = {}

        if query_params:
            # Process new format filters (f_* and op_* parameters)
            filter_keys = [k for k in query_params if k.startswith("f_")]

            for key in filter_keys[: settings.MAX_FILTER_PARAMS]:
                filter_column = key[2:]
                filter_operator = query_params.get(f"op_{filter_column}")
                filter_value = query_params.get(key)

                if filter_column and filter_operator and filter_value:
                    self.filters[filter_column] = ColumnFilterData(
                        column=filter_column,
                        operator=filter_operator,
                        value=filter_value,
                    )

        if column_filters:
            for item in column_filters:
                self.filters[item.column] = item

    @classmethod
    def from_request(cls, request) -> Self:
        query_params = request.GET
        if not any(key.startswith(("filter_", "f_")) for key in query_params):
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
        for filter_data in self.filters.values():
            query_value = filter_data.value
            if filter_data.operator in _LIST_OPERATORS:
                try:
                    parsed = json.loads(query_value)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, list):
                    # Use CSV writer to handle special characters in values
                    output = StringIO()
                    writer = csv.writer(output, delimiter="~")
                    writer.writerow(str(item) for item in parsed)
                    query_value = output.getvalue().rstrip("\r\n")

            query_data[f"f_{filter_data.column}"] = query_value
            query_data[f"op_{filter_data.column}"] = filter_data.operator
        return urlencode(query_data)
