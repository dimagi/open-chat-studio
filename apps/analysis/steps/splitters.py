from enum import StrEnum
from typing import Literal

import pandas as pd
from pandas.api import types as ptypes
from pandas.core.resample import TimeGrouper

from apps.analysis.core import BaseStep, Params, ParamsForm, StepContext, required
from apps.analysis.exceptions import StepError
from apps.analysis.steps.utils import format_truncated_date


class TimeGroup(StrEnum):
    secondly = "S"
    minutely = "T"
    hourly = "H"
    daily = "D"
    weekly = "W"
    monthly = "M"
    quarterly = "Q"
    yearly = "Y"

    def get_group_name(self, timestamp) -> str:
        return format_truncated_date(timestamp, self.name)


class TimeseriesSplitterParams(Params):
    time_group: required(TimeGroup) = None
    origin: required(Literal["start", "end"]) = None
    ignore_empty_groups: bool = True

    def get_dynamic_config_form_class(self) -> type[ParamsForm] | None:
        from .forms import TimeseriesSplitterParamsForm

        return TimeseriesSplitterParamsForm

    @property
    def grouper(self):
        return TimeGrouper(freq=self.time_group.value, origin=self.origin)


class TimeseriesSplitter(BaseStep[pd.DataFrame, dict[pd.Period, pd.DataFrame]]):
    """Splits input data by a time group to produce multiple output dataframes."""

    param_schema = TimeseriesSplitterParams
    input_type = pd.DataFrame
    output_type = pd.DataFrame

    def preflight_check(self, context: StepContext):
        if not ptypes.is_datetime64_any_dtype(context.data.index):
            raise StepError("Dataframe must have a datetime index")

    def run(self, params: TimeseriesSplitterParams, data: pd.DataFrame) -> list[StepContext[pd.DataFrame]]:
        grouped = data.groupby(params.grouper)
        results = []
        for origin, group in grouped:
            if params.ignore_empty_groups and not len(group):
                continue
            name = params.time_group.get_group_name(origin)
            results.append(StepContext(group, name=name))

        self.log.info(f"Split timeseries data into {len(results)} groups")
        for i, result in enumerate(results):
            self.log.info(f"    Group {i + 1}: {result.name} ({len(result.data)} rows)")

        return results
