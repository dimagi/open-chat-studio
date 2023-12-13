from datetime import datetime, timedelta

import pandas as pd
import pytest

from apps.analysis.core import PipelineContext
from apps.analysis.steps.filters import DurationUnit, TimeseriesFilter, TimeseriesFilterParams


def _make_params(duration_unit, duration_value, anchor_type, anchor_point=None):
    return TimeseriesFilterParams(
        duration_unit=duration_unit,
        duration_value=duration_value,
        anchor_type=anchor_type,
        anchor_point=datetime.fromisoformat(anchor_point or "2022-04-01"),
    )


@pytest.mark.parametrize(
    "params,start,end",
    [
        pytest.param(
            _make_params(DurationUnit.minutes, 1, "this"),
            "2022-04-01",
            "2022-04-01T00:01",
            id="minutes basic",
        ),
        pytest.param(
            _make_params(DurationUnit.minutes, 1, "this", "2022-04-01T00:00:04"),
            "2022-04-01",
            "2022-04-01T00:01",
            id="minutes,anchor_point rounding",
        ),
        pytest.param(
            _make_params(DurationUnit.minutes, 2, "this"),
            "2022-04-01",
            "2022-04-01T00:02",
            id="minutes duration value 2",
        ),
        pytest.param(
            _make_params(DurationUnit.minutes, 2, "last"),
            "2022-03-31T23:58",
            "2022-04-01",
            id="minutes last",
        ),
        pytest.param(
            _make_params(DurationUnit.minutes, 2, "last", "2022-04-01T00:00:04"),
            "2022-03-31T23:58",
            "2022-04-01",
            id="minutes last,anchor_point rounding",
        ),
        pytest.param(
            _make_params(DurationUnit.hours, 1, "this"),
            "2022-04-01",
            "2022-04-01T01:00",
            id="hours basic",
        ),
        pytest.param(
            _make_params(DurationUnit.hours, 1, "this", "2022-04-01T00:53"),
            "2022-04-01",
            "2022-04-01T01:00",
            id="hours,anchor_point rounding",
        ),
        pytest.param(
            _make_params(DurationUnit.days, 1, "this"),
            "2022-04-01",
            "2022-04-02",
            id="days basic",
        ),
        pytest.param(
            _make_params(DurationUnit.days, 1, "this", "2022-04-01T03:12"),
            "2022-04-01",
            "2022-04-02",
            id="days,anchor_point rounding",
        ),
        pytest.param(
            _make_params(DurationUnit.weeks, 1, "this"),
            "2022-03-28",
            "2022-04-04",
            id="weeks, anchor point on Monday",
        ),
        pytest.param(
            _make_params(DurationUnit.months, 1, "this"),
            "2022-04-01",
            "2022-05-01",
            id="months basic",
        ),
        pytest.param(
            _make_params(DurationUnit.months, 1, "this", "2022-04-03"),
            "2022-04-01",
            "2022-05-01",
            id="months,anchor_point rounding",
        ),
        pytest.param(
            _make_params(DurationUnit.years, 1, "this"),
            "2022-01-01",
            "2023-01-01",
            id="years, anchor point on Jan 1",
        ),
    ],
)
def test_timeseries_filter_params(params, start, end):
    assert params.start == datetime.fromisoformat(start)
    assert params.end == datetime.fromisoformat(end)


@pytest.fixture
def timeseries_data():
    dates = pd.date_range(start="2021-01-01", end="2021-01-31")
    data = pd.DataFrame(index=dates, data={"value": range(len(dates))})
    return data


@pytest.fixture
def timeseries_filter():
    step = TimeseriesFilter()
    step.initialize(PipelineContext())
    return step


def test_timeseries_filter_with_valid_params(timeseries_filter, timeseries_data):
    params = TimeseriesFilterParams(
        duration_unit=DurationUnit.days,
        duration_value=7,
        anchor_type="this",
        anchor_point=datetime.fromisoformat("2021-01-02"),
    )
    result = timeseries_filter.run(params, timeseries_data)
    assert len(result.data) == 7
    assert result.data["value"].tolist() == list(range(1, 8))


def test_timeseries_filter_with_empty_data(timeseries_filter):
    params = TimeseriesFilterParams(duration_unit=DurationUnit.days, duration_value=7, anchor_type="this")
    empty_data = pd.DataFrame()
    result = timeseries_filter.run(params, empty_data)
    assert len(result.data) == 0


def test_timeseries_filter_with_future_dates(timeseries_filter, timeseries_data):
    params = TimeseriesFilterParams(
        duration_unit=DurationUnit.days,
        duration_value=7,
        anchor_type="this",
        anchor_point=datetime.now() + timedelta(days=10),
    )
    result = timeseries_filter.run(params, timeseries_data)
    assert len(result.data) == 0
