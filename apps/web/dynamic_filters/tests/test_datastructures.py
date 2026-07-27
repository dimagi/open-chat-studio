from urllib.parse import parse_qs

import pytest
from django.http import QueryDict

from apps.web.dynamic_filters.datastructures import ColumnFilterData, FilterParams


def _querydict(params: dict) -> QueryDict:
    query_dict = QueryDict("", mutable=True)
    query_dict.update(params)
    return query_dict


def test_multiple_filters_on_the_same_column_are_all_retained():
    """Two filters on one column (e.g. a date range built from `after` + `before`) must both survive."""
    params = _querydict(
        {
            "filter_0_column": "first_message",
            "filter_0_operator": "after",
            "filter_0_value": "2026-04-30",
            "filter_1_column": "first_message",
            "filter_1_operator": "before",
            "filter_1_value": "2026-06-01",
        }
    )
    filter_params = FilterParams(params)

    assert [(f.operator, f.value) for f in filter_params.get_all("first_message")] == [
        ("after", "2026-04-30"),
        ("before", "2026-06-01"),
    ]


def test_get_all_returns_empty_list_for_unknown_column():
    assert FilterParams().get_all("first_message") == []


def test_to_query_round_trips_duplicate_columns():
    column_filters = [
        ColumnFilterData(column="first_message", operator="after", value="2026-04-30"),
        ColumnFilterData(column="first_message", operator="before", value="2026-06-01"),
        ColumnFilterData(column="participant", operator="equals", value="bob"),
    ]
    query = FilterParams(column_filters=column_filters).to_query()

    round_tripped = FilterParams(QueryDict(query))
    assert [(f.column, f.operator, f.value) for f in round_tripped.filters] == [
        (f.column, f.operator, f.value) for f in column_filters
    ]
    # each filter gets its own index so nothing is silently dropped
    assert sorted(parse_qs(query)["filter_0_column"] + parse_qs(query)["filter_1_column"]) == [
        "first_message",
        "first_message",
    ]


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        pytest.param("any of", '["a", "b"]', '["a", "b"]', id="json-list-untouched"),
        pytest.param("any of", "a", '["a"]', id="bare-string-wrapped"),
        pytest.param("equals", "a", "a", id="non-list-operator-untouched"),
    ],
)
def test_column_filter_data_normalises_list_values(operator, value, expected):
    assert ColumnFilterData(column="tags", operator=operator, value=value).value == expected
