import csv
import json
from io import StringIO
from typing import Self
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.http import QueryDict
from pydantic import BaseModel, model_validator

_LIST_OPERATORS = frozenset({"any of", "all of", "excludes"})


def _parse_csv_tilde_values(value: str) -> list[str]:
    if not isinstance(value, str):
        return []

    reader = csv.reader(StringIO(value), delimiter="~", quotechar='"')
    try:
        row = next(reader)
    except StopIteration:
        return []
    return row


def _serialize_csv_tilde_values(values: list[str] | tuple[str, ...] | list[object]) -> str:
    output = StringIO()
    writer = csv.writer(output, delimiter="~", quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator="")
    writer.writerow([str(item) for item in values])
    return output.getvalue().rstrip("\r\n")


def _try_parse_json_string_list(value: str) -> list[str] | None:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None

    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return parsed
    return None


def _coerce_list_filter_values(value: str) -> list[str] | None:
    parsed = _try_parse_json_string_list(value)
    if parsed is not None:
        if len(parsed) == 1:
            if inner := _try_parse_json_string_list(parsed[0]):
                return inner
        return parsed
    return None


class ColumnFilterData(BaseModel):
    """Data class representing a single column's filter data."""

    column: str
    operator: str
    value: str

    @model_validator(mode="after")
    def _normalize_list_value(self) -> Self:
        """Normalize list-based filter values to JSON arrays for operators that expect lists."""
        if self.operator in _LIST_OPERATORS:
            if isinstance(self.value, str):
                if "~" in self.value:
                    parsed = _coerce_list_filter_values(self.value)
                    if parsed is not None:
                        self.value = json.dumps(parsed)
                    else:
                        parsed = _parse_csv_tilde_values(self.value)
                        if parsed:
                            self.value = json.dumps(parsed)
                        else:
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
                if parsed := _coerce_list_filter_values(query_value):
                    query_value = _serialize_csv_tilde_values(parsed)

            query_data[f"f_{filter_data.column}"] = query_value
            query_data[f"op_{filter_data.column}"] = filter_data.operator
        return urlencode(query_data)
