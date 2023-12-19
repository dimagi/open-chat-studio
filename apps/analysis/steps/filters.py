from datetime import datetime
from enum import IntEnum
from functools import cached_property
from typing import Annotated, Any, Literal

import pandas as pd
from dateutil.relativedelta import relativedelta
from pandas.api import types as ptypes
from pydantic import Field, PositiveInt

from apps.analysis.core import BaseStep, Params, ParamsForm, StepContext, required
from apps.analysis.exceptions import StepError
from apps.analysis.steps.utils import format_truncated_date


class DurationUnit(IntEnum):
    minutes = 0
    hours = 1
    days = 2
    weeks = 3
    months = 4
    years = 5

    def delta(self, quantity: int):
        return relativedelta(**{self.name: quantity})


class TimeseriesFilterParams(Params):
    """Filter a timeseries dataframe by a duration and anchor point.
    This creates a time window either before or after the anchor point. The length
    of the window is determined by the duration and duration unit."""

    duration_unit: required(DurationUnit) = None
    duration_value: required(PositiveInt) = None
    anchor_type: Literal["this", "last"] = "this"
    anchor_mode: Literal["relative_start", "relative_end", "absolute"] = "relative_start"
    anchor_point: Annotated[datetime, Field(default_factory=datetime.utcnow)]
    minimum_data_points: int = 10
    calendar_time: bool = False

    def model_post_init(self, __context: Any) -> None:
        if self.anchor_mode != "absolute":
            self.calendar_time = False

        if self.anchor_mode == "relative_end":
            self.anchor_type = "last"

    @cached_property
    def range_tuple(self) -> tuple[datetime, datetime]:
        start = self.anchor_point
        if self.calendar_time:
            start = start + self.anchor_adjustment()
        if self.anchor_type == "last":
            start -= self.duration_unit.delta(self.duration_value)
        return start, start + self.delta()

    @property
    def start(self):
        return self.range_tuple[0]

    @property
    def end(self):
        return self.range_tuple[1]

    def period_name(self):
        truncate_to = self.duration_unit.name
        return f"{format_truncated_date(self.start, truncate_to)}--{format_truncated_date(self.end, truncate_to)}"

    def filter(self, date: datetime) -> bool:
        start, end = self.range_tuple
        return start <= date < end

    def delta(self) -> relativedelta:
        return self.duration_unit.delta(self.duration_value)

    def anchor_adjustment(self) -> relativedelta:
        delta = relativedelta(second=0, microsecond=0)
        if self.duration_unit >= DurationUnit.hours:
            delta += relativedelta(minute=0)
        if self.duration_unit >= DurationUnit.days:
            delta += relativedelta(hour=0)

        if self.duration_unit == DurationUnit.weeks:
            return relativedelta(weeks=-1, weekday=0)

        if self.duration_unit >= DurationUnit.months:
            delta += relativedelta(day=1)
        if self.duration_unit >= DurationUnit.years:
            delta += relativedelta(month=1)
        return delta

    def get_dynamic_config_form_class(self) -> type[ParamsForm] | None:
        from apps.analysis.steps.forms import TimeseriesFilterForm

        return TimeseriesFilterForm


class TimeseriesStep(BaseStep[pd.DataFrame, pd.DataFrame]):
    """Base class for steps that operate on timeseries data."""

    input_type = pd.DataFrame
    output_type = pd.DataFrame

    def preflight_check(self, context: StepContext):
        if not ptypes.is_datetime64_any_dtype(context.data.index):
            raise StepError("Dataframe must have a datetime index")


class TimeseriesFilter(TimeseriesStep):
    """Filter timeseries data based on the date index to extract a time window."""

    param_schema = TimeseriesFilterParams
    input_type = pd.DataFrame
    output_type = pd.DataFrame

    def run(self, params: TimeseriesFilterParams, context: StepContext[pd.DataFrame]) -> StepContext[pd.DataFrame]:
        data = context.get_data()
        self.log.info(f"Initial timeseries data from {data.index.min()} to {data.index.max()} ({len(data)} rows)")

        if params.anchor_mode == "relative_start":
            params.anchor_point = data.index.min()
            self.log.debug(f"Setting anchor point to start of data {params.anchor_point}")
        elif params.anchor_mode == "relative_end":
            params.anchor_point = data.index.max()
            self.log.debug(f"Setting anchor point to end of data {params.anchor_point}")

        if params.anchor_type == "this":
            result = data.loc[(data.index >= params.start) & (data.index < params.end)]
        else:
            result = data.loc[(data.index > params.start) & (data.index <= params.end)]
        self.log.info(f"Filtered timeseries data from {params.start} to {params.end} ({len(result)} rows)")
        if len(result) < params.minimum_data_points:
            raise StepError(
                f"Filtered timeseries data has {len(result)} rows, but minimum is {params.minimum_data_points}."
            )
        return StepContext(result, name=params.period_name())
