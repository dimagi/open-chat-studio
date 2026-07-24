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
    """Parse the tilde-delimited CSV wire format into a list of values.

    Inverse of :func:`serialize_csv_tilde_values`. Input is expected to originate from that
    serializer, so well-formed round-trips are exact. Hand-crafted or malformed input (e.g. an
    unbalanced quote) is parsed best-effort and never raises — if the CSV reader chokes, the raw
    string is returned as a single value rather than propagating an error.
    """
    if not isinstance(value, str):
        return []

    reader = csv.reader(StringIO(value), delimiter="~", quotechar='"')
    try:
        row = next(reader)
    except StopIteration:
        return []
    except csv.Error:
        return [value]
    return row


def serialize_csv_tilde_values(values: list[str] | tuple[str, ...] | list[object]) -> str:
    """Serialize a list of values into the tilde-delimited CSV wire format.

    This is the single source of truth for the ``f_*`` list-value wire format and is the
    inverse of :func:`_parse_csv_tilde_values`. Values containing the ``~`` separator are
    quoted so they round-trip correctly (e.g. ``["tag~2", "a"]`` -> ``"tag~2"~a``).
    """
    output = StringIO()
    writer = csv.writer(output, delimiter="~", quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator="")
    writer.writerow([str(item) for item in values])
    return output.getvalue().rstrip("\r\n")


def _try_parse_json_string_list(value: str) -> list[object] | None:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None

    if isinstance(parsed, list):
        return parsed
    return None


def _coerce_list_filter_values(value: str) -> list[object] | None:
    """Return ``value`` parsed as a JSON list, or None if it is not a JSON-list string."""
    return _try_parse_json_string_list(value)


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
                parsed = _coerce_list_filter_values(self.value)
                if parsed is not None:
                    self.value = json.dumps(parsed)
                else:
                    # Always CSV-parse: the wire format quotes values containing "~" or '"'
                    # (matching the frontend serializer), and a bare value without delimiters
                    # parses back to itself. Only fall back for an empty value.
                    parsed = _parse_csv_tilde_values(self.value)
                    self.value = json.dumps(parsed if parsed else [self.value])
            else:
                self.value = json.dumps([str(self.value)])
        return self

    def __bool__(self):
        return bool(self.column and self.operator and self.value)


def _translate_legacy_query_params(query_params: QueryDict) -> QueryDict:
    """Translate legacy ``filter_<n>_*`` query params into the new f_/op_ format.

    Keeps bookmarked or externally-generated legacy URLs working on read; params that are
    already in the new format (or contain no filters) are returned unchanged. Imported
    locally to avoid a circular import with filter_format, which imports
    serialize_csv_tilde_values from this module.
    """
    # Local import avoids a circular import: filter_format imports from this module.
    from .filter_format import convert_saved_filter_data, is_legacy_filter_data  # noqa: PLC0415

    if is_legacy_filter_data(query_params):
        return QueryDict(convert_saved_filter_data(query_params.urlencode()))
    return query_params


class FilterParams:
    """A container for filter parameters extracted from a request's query parameters.

    The ``f_`` and ``op_`` query-string prefixes are RESERVED for dynamic filters: every
    param starting with ``f_`` is treated as a filter column (``f_<column>`` holds the value,
    ``op_<column>`` holds the operator). Do not introduce unrelated query params with these
    prefixes on any page that renders a filterable table, or they will be misread as filters.
    """

    def __init__(self, query_params: QueryDict | None = None, column_filters: list[ColumnFilterData] | None = None):
        self.filters: dict[str, ColumnFilterData] = {}

        if query_params:
            # Process new format filters (f_* and op_* parameters). The f_/op_ prefixes are
            # reserved — any query param beginning with f_ is interpreted as a filter column.
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
        query_params = _translate_legacy_query_params(request.GET)
        if not any(key.startswith("f_") for key in query_params):
            return cls.from_request_header(request, "HX-Current-URL")
        return cls(query_params)

    @classmethod
    def from_request_header(cls, request, header: str):
        if header_value := request.headers.get(header):
            parsed_url = urlparse(header_value)
            return cls(_translate_legacy_query_params(QueryDict(parsed_url.query)))
        return cls()

    def get(self, column: str) -> ColumnFilterData | None:
        return self.filters.get(column)

    def to_query(self) -> str:
        query_data = {}
        for filter_data in self.filters.values():
            query_value = filter_data.value
            if filter_data.operator in _LIST_OPERATORS:
                if parsed := _coerce_list_filter_values(query_value):
                    query_value = serialize_csv_tilde_values(parsed)

            query_data[f"f_{filter_data.column}"] = query_value
            query_data[f"op_{filter_data.column}"] = filter_data.operator
        return urlencode(query_data)
