from django.http import QueryDict

from apps.web.dynamic_filters.datastructures import ColumnFilterData, FilterParams


def test_column_filter_data():
    assert bool(ColumnFilterData(column="test", operator="equals", value="value")) is True
    assert bool(ColumnFilterData(column="", operator="equals", value="value")) is False
    assert bool(ColumnFilterData(column="test", operator="", value="value")) is False
    assert bool(ColumnFilterData(column="test", operator="equals", value="")) is False
    assert bool(ColumnFilterData(column="", operator="equals", value="value")) is False
    assert bool(ColumnFilterData(column="test", operator="", value="value")) is False
    assert bool(ColumnFilterData(column="test", operator="equals", value="")) is False


def test_column_filter_data_list_normalization():
    """Test that list operators normalize tilde-separated values."""
    # JSON array input
    cf = ColumnFilterData(column="tags", operator="any of", value='["tag1", "tag2"]')
    assert cf.value == '["tag1", "tag2"]'

    # Tilde-separated values
    cf = ColumnFilterData(column="tags", operator="any of", value="tag1~tag2")
    assert cf.value == '["tag1", "tag2"]'

    # Single value
    cf = ColumnFilterData(column="tags", operator="any of", value="tag1")
    assert cf.value == '["tag1"]'

    # Values with quoted strings containing tildes
    cf = ColumnFilterData(column="tags", operator="any of", value='tag1~"tag~2"~tag3')
    assert cf.value == '["tag1", "tag~2", "tag3"]'


def test_filter_params_parse_new_format():
    """Test parsing new format (f_* and op_*)."""
    query_dict = QueryDict("f_tags=tag1~tag2&op_tags=any%20of&f_status=active&op_status=equals")
    params = FilterParams(query_dict)

    assert len(params.filters) == 2
    assert params.get("tags").column == "tags"
    assert params.get("tags").operator == "any of"
    assert params.get("tags").value == '["tag1", "tag2"]'

    assert params.get("status").column == "status"
    assert params.get("status").operator == "equals"
    assert params.get("status").value == "active"


def test_filter_params_to_query():
    """Test generating new format query string."""
    params = FilterParams()
    params.filters["tags"] = ColumnFilterData(column="tags", operator="any of", value='["tag1", "tag2"]')
    params.filters["status"] = ColumnFilterData(column="status", operator="equals", value="active")

    query_string = params.to_query()

    # Parse the generated query string to verify
    query_dict = QueryDict(query_string)
    assert query_dict.get("f_tags") == "tag1~tag2"
    assert query_dict.get("op_tags") == "any of"
    assert query_dict.get("f_status") == "active"
    assert query_dict.get("op_status") == "equals"


def test_filter_params_to_query_with_special_chars():
    """Test generating query string with values containing special characters."""
    params = FilterParams()
    params.filters["tags"] = ColumnFilterData(column="tags", operator="any of", value='["tag~1", "tag2"]')

    query_string = params.to_query()

    # Parse the generated query string to verify
    query_dict = QueryDict(query_string)
    # The value should be quoted because it contains ~
    assert query_dict.get("f_tags") is not None
    assert "~" in query_dict["f_tags"]

    # Now parse it back and verify it normalizes correctly
    params2 = FilterParams(query_dict)
    assert params2.get("tags").value == '["tag~1", "tag2"]'
