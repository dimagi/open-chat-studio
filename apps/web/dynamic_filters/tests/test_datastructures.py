import json

import pytest
from django.http import QueryDict

from apps.web.dynamic_filters.datastructures import (
    ColumnFilterData,
    FilterParams,
    serialize_csv_tilde_values,
)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        pytest.param("hello", ["hello"], id="bare-single-value"),
        pytest.param("tag1~tag2", ["tag1", "tag2"], id="unquoted-multi-value"),
        pytest.param('"tag~2"~a', ["tag~2", "a"], id="quoted-separator-in-value"),
        pytest.param('"fo""o"', ['fo"o'], id="quoted-quote-in-value"),
        pytest.param('["tag1", "tag2"]', ["tag1", "tag2"], id="json-list"),
    ],
)
def test_normalize_list_value_parses_wire_format(raw_value, expected):
    """List operators must decode every wire form the serializer can produce."""
    column_filter = ColumnFilterData(column="tags", operator="any of", value=raw_value)
    assert json.loads(column_filter.value) == expected


@pytest.mark.parametrize(
    "values",
    [
        pytest.param(["tag1", "tag2"], id="plain"),
        pytest.param(["tag~2", "a"], id="separator-in-value"),
        pytest.param(['fo"o', "a"], id="quote-in-value"),
        pytest.param(["a~b", 'c"d'], id="both-special-chars"),
    ],
)
def test_serialize_normalize_round_trip(values):
    """A list serialized to the wire format must decode back to the original list."""
    wire_value = serialize_csv_tilde_values(values)
    column_filter = ColumnFilterData(column="tags", operator="any of", value=wire_value)
    assert json.loads(column_filter.value) == values


def test_to_query_round_trips_special_characters():
    """Python -> query string -> Python must preserve values containing ~ and quotes."""
    values = ["tag~2", 'fo"o']
    params = FilterParams(
        column_filters=[
            ColumnFilterData(column="tags", operator="any of", value=json.dumps(values)),
        ]
    )

    query_string = params.to_query()

    reparsed = FilterParams(QueryDict(query_string))
    assert json.loads(reparsed.get("tags").value) == values
