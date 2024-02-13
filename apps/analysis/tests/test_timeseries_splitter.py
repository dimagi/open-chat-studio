import pandas as pd
import pytest
from pandas import DataFrame, Timestamp, date_range

from apps.analysis.core import PipelineContext, StepContext
from apps.analysis.exceptions import StepError
from apps.analysis.steps.splitters import TimeGroup, TimeseriesSplitter, TimeseriesSplitterParams


@pytest.fixture()
def timeseries_splitter():
    step = TimeseriesSplitter()
    step.initialize(PipelineContext())
    return step


@pytest.fixture()
def timeseries_data():
    dates = date_range(start="1/1/2021", end="2/15/2021")
    data = DataFrame(index=dates, data={"value": range(len(dates))})
    return data


@pytest.mark.parametrize(
    ("time_group", "expected_groups", "group_lengths"),
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
    result = timeseries_splitter.run(params, StepContext(timeseries_data))
    assert len(result) == expected_groups
    assert [len(res.data) for res in result] == group_lengths


@pytest.mark.parametrize(
    ("origin", "expected_groups"),
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
    result = timeseries_splitter.run(params, StepContext(data))
    assert len(result) == 2
    assert list(result[0].data.index) == [pd.Timestamp(d) for d in expected_groups[0]]
    assert list(result[1].data.index) == [pd.Timestamp(d) for d in expected_groups[1]]


def test_timeseries_splitter_raises_error_with_non_datetime_index(timeseries_splitter):
    data = DataFrame(data={"value": range(31)})
    with pytest.raises(StepError):
        timeseries_splitter.preflight_check(StepContext.initial(data))


@pytest.mark.parametrize(
    ("ignore_empty", "group_count"),
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
    result = timeseries_splitter.run(params, StepContext(data))
    assert len(result) == group_count


@pytest.mark.parametrize(
    ("time_group", "expected"),
    [
        (TimeGroup.secondly, "2022-03-07T15:22:05"),
        (TimeGroup.minutely, "2022-03-07T15:22"),
        (TimeGroup.hourly, "2022-03-07T15"),
        (TimeGroup.daily, "2022-03-07"),
        (TimeGroup.weekly, "2022-W10"),
        (TimeGroup.monthly, "2022-03"),
        (TimeGroup.quarterly, "2022-Q1"),
        (TimeGroup.yearly, "2022"),
    ],
)
def test_time_group_get_group_name(time_group, expected):
    timestamp = Timestamp("2022-03-07 15:22:05")
    assert time_group.get_group_name(timestamp) == expected
