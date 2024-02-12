from datetime import datetime, timedelta

import pandas as pd
import pytest

from apps.analysis.core import PipelineContext, StepContext
from apps.analysis.log import Logger, StdoutLogStream
from apps.analysis.steps.filters import DurationUnit, TimeseriesFilter, TimeseriesFilterParams


def _make_params(
    duration_unit, duration_value, anchor_type="this", anchor_point=None, calendar_time=True, anchor_mode="absolute"
):
    return TimeseriesFilterParams(
        duration_unit=duration_unit,
        duration_value=duration_value,
        anchor_type=anchor_type,
        anchor_point=datetime.fromisoformat(anchor_point or "2022-04-01"),
        anchor_mode=anchor_mode,
        calendar_time=calendar_time,
        minimum_data_points=1,
    )


@pytest.mark.parametrize(
    ("params", "start", "end"),
    [
        pytest.param(
            _make_params(DurationUnit.minutes, 2, "this"),
            "2022-04-01",
            "2022-04-01T00:02",
            id="minutes basic",
        ),
        pytest.param(
            _make_params(DurationUnit.minutes, 2, "this", "2022-04-01T00:00:04"),
            "2022-04-01",
            "2022-04-01T00:02",
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
            _make_params(DurationUnit.hours, 2, "last"),
            "2022-03-31T22:00",
            "2022-04-01T00:00",
            id="hours last",
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
            _make_params(DurationUnit.days, 2, "last"),
            "2022-03-30",
            "2022-04-01",
            id="days last",
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
            _make_params(DurationUnit.weeks, 2, "this", anchor_mode="relative_start"),
            "2022-04-01",
            "2022-04-15",
            id="weeks, relative_start, this",
        ),
        pytest.param(
            _make_params(DurationUnit.weeks, 2, "last", anchor_mode="relative_end"),
            "2022-03-18",
            "2022-04-01",
            id="weeks, relative_end, last",
        ),
        pytest.param(
            _make_params(DurationUnit.months, 1, "this"),
            "2022-04-01",
            "2022-05-01",
            id="months basic",
        ),
        pytest.param(
            _make_params(DurationUnit.months, 2, "last"),
            "2022-02-01",
            "2022-04-01",
            id="months last",
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
        pytest.param(
            _make_params(DurationUnit.years, 1, "this", calendar_time=False),
            "2022-04-01",
            "2023-04-01",
            id="years, not calendar time",
        ),
        pytest.param(
            _make_params(DurationUnit.years, 2, "last"),
            "2020-01-01",
            "2022-01-01",
            id="years last",
        ),
        pytest.param(
            _make_params(DurationUnit.years, 2, "last", anchor_mode="relative_end", calendar_time=False),
            "2020-04-01",
            "2022-04-01",
            id="years last, relative_end",
        ),
    ],
)
def test_timeseries_filter_params(params, start, end):
    assert params.start == datetime.fromisoformat(start)
    assert params.end == datetime.fromisoformat(end)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [
        (DurationUnit.minutes, 7, "2022-04-01T15:36--2022-04-01T15:43"),
        (DurationUnit.hours, 7, "2022-04-01T15--2022-04-01T22"),
        (DurationUnit.days, 7, "2022-04-01--2022-04-08"),
        (DurationUnit.weeks, 7, "2022-W13--2022-W20"),
        (DurationUnit.months, 7, "2022-04--2022-11"),
        (DurationUnit.years, 7, "2022--2029"),
    ],
)
def test_timeseries_filter_params_period_name(unit, value, expected):
    params = _make_params(unit, value, anchor_point="2022-04-01T15:36:45.123456")
    assert params.period_name() == expected


@pytest.fixture()
def timeseries_data():
    dates = pd.date_range(start="2021-01-01", end="2021-01-31")
    data = pd.DataFrame(index=dates, data={"value": range(len(dates))})
    return data


@pytest.fixture()
def timeseries_filter():
    step = TimeseriesFilter()
    step.initialize(PipelineContext(log=Logger(StdoutLogStream())))
    return step


def test_timeseries_filter_with_valid_params(timeseries_filter, timeseries_data):
    params = TimeseriesFilterParams(
        duration_unit=DurationUnit.days,
        duration_value=7,
        anchor_type="this",
        anchor_mode="absolute",
        anchor_point=datetime.fromisoformat("2021-01-02"),
        minimum_data_points=1,
        calendar_time=True,
    )
    result = timeseries_filter.run(params, StepContext(timeseries_data))
    assert len(result.data) == 7
    assert result.data["value"].tolist() == list(range(1, 8))


def test_timeseries_filter_with_empty_data(timeseries_filter):
    params = TimeseriesFilterParams(
        duration_unit=DurationUnit.days,
        duration_value=7,
        anchor_type="this",
        anchor_mode="absolute",
        minimum_data_points=0,
    )
    empty_data = pd.DataFrame()
    result = timeseries_filter.run(params, StepContext(empty_data))
    assert len(result.data) == 0


def test_timeseries_filter_with_future_dates(timeseries_filter, timeseries_data):
    params = TimeseriesFilterParams(
        duration_unit=DurationUnit.days,
        duration_value=7,
        anchor_type="this",
        anchor_point=datetime.now() + timedelta(days=10),
        anchor_mode="absolute",
        minimum_data_points=0,
    )
    result = timeseries_filter.run(params, StepContext(timeseries_data))
    assert len(result.data) == 0


@pytest.mark.parametrize(
    ("anchor_type", "anchor_mode", "expected"),
    [
        ("this", "absolute", [1, 2]),
        ("this", "relative_start", [0, 1]),
        ("last", "relative_end", [29, 30]),
    ],
)
def test_timeseries_filter_anchor_mode(anchor_type, anchor_mode, expected, timeseries_filter, timeseries_data):
    params = TimeseriesFilterParams(
        duration_unit=DurationUnit.days,
        duration_value=2,
        anchor_type=anchor_type,
        anchor_mode=anchor_mode,
        anchor_point=datetime.fromisoformat("2021-01-02"),
        minimum_data_points=0,
    )
    result = timeseries_filter.run(params, StepContext(timeseries_data))
    assert len(result.data) == 2
    assert result.data["value"].tolist() == expected


def test_timeseries_filter_form():
    form_cls = TimeseriesFilterParams().get_dynamic_config_form_class()
    form = form_cls(None, data={"duration_unit": 2, "duration_value": 2, "anchor_mode": "relative_end"})
    assert form.is_valid(), form.errors
    params = form.get_params()
    assert params.anchor_mode == "relative_end"
    assert params.anchor_type == "last"
    assert not params.calendar_time
