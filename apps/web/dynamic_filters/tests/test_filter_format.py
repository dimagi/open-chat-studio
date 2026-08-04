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
    assert json.loads(filter_params.get_all("tags")[0].value) == ["tag~2", "a"]


def test_convert_saved_filter_data_leaves_new_format_unchanged():
    """Already-converted strings (even when leading with a non-filter param) are untouched."""
    already_new = "page=2&f_tags=x&op_tags=any+of"
    assert convert_saved_filter_data(already_new) == already_new


def test_convert_saved_filter_data_preserves_two_filters_on_one_column():
    """Regression test: a date range is two filters on one column and both bounds must survive.

    The legacy format keyed filters by position, so the same column could appear twice. The new
    format keys by column, which only expresses two filters as a repeated key -- a plain dict
    keyed by column would keep the last bound and silently widen the range.
    """
    legacy_query_string = urlencode(
        {
            "filter_0_column": "first_message",
            "filter_0_operator": "after",
            "filter_0_value": "2026-01-01",
            "filter_1_column": "first_message",
            "filter_1_operator": "before",
            "filter_1_value": "2026-02-01",
        }
    )

    converted = QueryDict(convert_saved_filter_data(legacy_query_string))

    assert converted.getlist("f_first_message") == ["2026-01-01", "2026-02-01"]
    assert converted.getlist("op_first_message") == ["after", "before"]

    # And both bounds survive as distinct filters once parsed.
    assert [(f.operator, f.value) for f in FilterParams(converted).get_all("first_message")] == [
        ("after", "2026-01-01"),
        ("before", "2026-02-01"),
    ]


def test_convert_saved_filter_data_dict_form_preserves_repeated_column():
    """The dict form lists the values of a repeated column rather than collapsing them."""
    legacy_filter_data = {
        "filter_0_column": "first_message",
        "filter_0_operator": "after",
        "filter_0_value": "2026-01-01",
        "filter_1_column": "first_message",
        "filter_1_operator": "before",
        "filter_1_value": "2026-02-01",
    }

    assert convert_saved_filter_data(legacy_filter_data) == {
        "f_first_message": ["2026-01-01", "2026-02-01"],
        "op_first_message": ["after", "before"],
    }


def test_convert_saved_filter_data_skips_filter_missing_its_operator():
    """A filter without an operator is dropped so the f_/op_ lists cannot fall out of step.

    The pair of lists is zipped positionally when parsed, so emitting a value with no matching
    operator would pair every later value with the wrong operator.
    """
    legacy_filter_data = {
        "filter_0_column": "first_message",
        "filter_0_value": "2026-01-01",
        "filter_1_column": "first_message",
        "filter_1_operator": "before",
        "filter_1_value": "2026-02-01",
    }

    assert convert_saved_filter_data(legacy_filter_data) == {
        "f_first_message": "2026-02-01",
        "op_first_message": "before",
    }
