import json

import pytest
from django.http import QueryDict
from django.test import RequestFactory

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
    "malformed_value",
    [
        pytest.param('"tag~a', id="unbalanced-leading-quote"),
        pytest.param('a~"b', id="unbalanced-trailing-quote"),
        pytest.param('"', id="lone-quote"),
    ],
)
def test_normalize_list_value_handles_malformed_input(malformed_value):
    """Hand-crafted/malformed wire values must parse best-effort without raising."""
    column_filter = ColumnFilterData(column="tags", operator="any of", value=malformed_value)
    # Result is a JSON list (deterministic best-effort), never an exception.
    assert isinstance(json.loads(column_filter.value), list)


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


def test_single_json_string_value_is_not_unwrapped():
    """A single selected value that is itself a valid JSON-list string must stay one value.

    Regression: the old single-element unwrap turned ``["[1, 2]"]`` into ``[1, 2]``, silently
    splitting one legitimate value into two.
    """
    column_filter = ColumnFilterData(column="tags", operator="any of", value=json.dumps(["[1, 2]"]))
    assert json.loads(column_filter.value) == ["[1, 2]"]


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


def test_from_request_translates_legacy_url_params():
    """Bookmarked/legacy filter_<n>_* URLs must still filter (read shim), not silently drop."""
    request = RequestFactory().get(
        "/",
        data={
            "filter_0_column": "experiment",
            "filter_0_operator": "any of",
            "filter_0_value": "tag1~tag2",
        },
    )

    params = FilterParams.from_request(request)

    column_filter = params.get("experiment")
    assert column_filter is not None
    assert column_filter.operator == "any of"
    assert json.loads(column_filter.value) == ["tag1", "tag2"]


def test_from_request_header_translates_legacy_url_params():
    """The HX-Current-URL fallback path must translate legacy params too."""
    legacy_url = "http://testserver/sessions/?filter_0_column=experiment&filter_0_operator=any+of&filter_0_value=5"
    request = RequestFactory().get("/", HTTP_HX_CURRENT_URL=legacy_url)

    params = FilterParams.from_request(request)

    column_filter = params.get("experiment")
    assert column_filter is not None
    assert json.loads(column_filter.value) == ["5"]


def test_from_request_leaves_new_format_untouched():
    """New-format URLs must pass through unchanged."""
    request = RequestFactory().get("/", data={"f_experiment": "5", "op_experiment": "any of"})

    params = FilterParams.from_request(request)

    assert json.loads(params.get("experiment").value) == ["5"]
