from apps.web.dynamic_filters.datastructures import ColumnFilterData


def test_column_filter_data():
    assert bool(ColumnFilterData(column="test", operator="equals", value="value")) is True
    assert bool(ColumnFilterData(column="", operator="equals", value="value")) is False
    assert bool(ColumnFilterData(column="test", operator="", value="value")) is False
    assert bool(ColumnFilterData(column="test", operator="equals", value="")) is False
    assert bool(ColumnFilterData(column="", operator="equals", value="value")) is False
    assert bool(ColumnFilterData(column="test", operator="", value="value")) is False
    assert bool(ColumnFilterData(column="test", operator="equals", value="")) is False
