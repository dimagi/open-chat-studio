from datetime import datetime

import pandas as pd
import pytest
from pandas import DataFrame, date_range

from apps.analysis.core import PipelineContext, StepContext
from apps.analysis.exceptions import StepError
from apps.analysis.steps.splitters import TimeGroup, TimeseriesSplitter, TimeseriesSplitterParams


@pytest.fixture
def timeseries_splitter():
    step = TimeseriesSplitter()
    step.initialize(PipelineContext(None))
    return step


@pytest.fixture
def timeseries_data():
    dates = date_range(start="1/1/2021", end="2/15/2021")
    data = DataFrame(index=dates, data={"value": range(len(dates))})
    return data


@pytest.mark.parametrize(
    "time_group, expected_groups, group_lengths",
    [
        (TimeGroup.daily, 46, [1] * 46),
        (TimeGroup.weekly, 8, [3, 7, 7, 7, 7, 7, 7, 1]),
        (TimeGroup.monthly, 2, [31, 15]),
    ],
)
def test_timeseries_splitter_splits_data_into_correct_groups(
    time_group, expected_groups, group_lengths, timeseries_splitter, timeseries_data
):
    params = TimeseriesSplitterParams(time_group=time_group, origin="start")
    result = timeseries_splitter.run(params, timeseries_data)
    assert len(result.data) == expected_groups
    assert [len(group) for group in result.data] == group_lengths


@pytest.mark.parametrize(
    "origin, expected_groups",
    [
        ("start", [["2021-01-01T11:30", "2021-01-01T12:15"], ["2021-01-01T12:30"]]),
        ("end", [["2021-01-01T11:30"], ["2021-01-01T12:15", "2021-01-01T12:30"]]),
    ],
)
def test_timeseries_splitter_splits_data_into_correct_groups_with_origin(origin, expected_groups, timeseries_splitter):
    dates = [
        pd.Timestamp("2021-01-01T11:30"),
        pd.Timestamp("2021-01-01T12:15"),
        pd.Timestamp("2021-01-01T12:30"),
    ]
    data = DataFrame(index=dates, data={"value": range(len(dates))})

    params = TimeseriesSplitterParams(time_group=TimeGroup.hourly, origin=origin)
    result = timeseries_splitter.run(params, data)
    assert len(result.data) == 2
    assert list(result.data[0].index) == [pd.Timestamp(d) for d in expected_groups[0]]
    assert list(result.data[1].index) == [pd.Timestamp(d) for d in expected_groups[1]]


def test_timeseries_splitter_raises_error_with_non_datetime_index(timeseries_splitter):
    data = DataFrame(data={"value": range(31)})
    with pytest.raises(StepError):
        timeseries_splitter.preflight_check(StepContext.initial(data))


@pytest.mark.parametrize(
    "ignore_empty, group_count",
    [
        (False, 4),
        (True, 3),
    ],
)
def test_timeseries_splitter_ignores_empty_groups(ignore_empty, group_count, timeseries_splitter):
    params = TimeseriesSplitterParams(time_group=TimeGroup.daily, origin="start", ignore_empty_groups=ignore_empty)
    dates = [
        pd.Timestamp("2021-01-01"),
        pd.Timestamp("2021-01-02"),
        pd.Timestamp("2021-01-04"),
    ]
    data = DataFrame(index=dates, data={"value": range(len(dates))})
    result = timeseries_splitter.run(params, data)
    assert len(result.data) == group_count
