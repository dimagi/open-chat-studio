import json
from urllib.parse import urlencode

from django.http import QueryDict

from apps.web.dynamic_filters.datastructures import FilterParams
from apps.web.dynamic_filters.filter_format import convert_saved_filter_data


def test_convert_saved_filter_data_to_new_format():
    """Legacy filter payloads should be converted to the new f_/op_ query-style format."""
    legacy_filter_data = {
        "filter_0_column": "status",
        "filter_0_operator": "equals",
        "filter_0_value": "active",
        "filter_1_column": "tags",
        "filter_1_operator": "any of",
        "filter_1_value": '["tag1", "tag2"]',
    }

    converted = convert_saved_filter_data(legacy_filter_data)

    assert converted == {
        "f_status": "active",
        "op_status": "equals",
        "f_tags": "tag1~tag2",
        "op_tags": "any of",
    }


def test_convert_saved_filter_data_accepts_query_string():
    """A raw legacy query string should be converted and returned as a query string."""
    legacy_query_string = urlencode(
        {
            "filter_0_column": "status",
            "filter_0_operator": "equals",
            "filter_0_value": "active",
            "filter_1_column": "tags",
            "filter_1_operator": "any of",
            "filter_1_value": '["tag1", "tag2"]',
        }
    )

    converted = convert_saved_filter_data(legacy_query_string)

    assert dict(QueryDict(converted).items()) == {
        "f_status": "active",
        "op_status": "equals",
        "f_tags": "tag1~tag2",
        "op_tags": "any of",
    }


def test_convert_saved_filter_data_round_trips_separator_in_value():
    """A value containing the ~ separator must survive conversion and FilterParams parsing."""
    legacy_query_string = urlencode(
        {
            "filter_0_column": "tags",
            "filter_0_operator": "any of",
            "filter_0_value": '["tag~2", "a"]',
        }
    )

    converted = convert_saved_filter_data(legacy_query_string)

    # The separator inside the value is quoted so it does not split into extra items.
    assert QueryDict(converted)["f_tags"] == '"tag~2"~a'

    # And the new parser reads the exact original list back out.
    filter_params = FilterParams(QueryDict(converted))
    assert json.loads(filter_params.get("tags").value) == ["tag~2", "a"]


def test_convert_saved_filter_data_leaves_new_format_unchanged():
    """Already-converted strings (even when leading with a non-filter param) are untouched."""
    already_new = "page=2&f_tags=x&op_tags=any+of"
    assert convert_saved_filter_data(already_new) == already_new
