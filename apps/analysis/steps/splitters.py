from datetime import datetime
from enum import StrEnum
from typing import Literal

import pandas as pd
from pandas.api import types as ptypes
from pandas.core.resample import TimeGrouper

from ..core import BaseStep, Params, ParamsForm, StepContext, StepError


class TimeGroup(StrEnum):
    secondly = "S"
    minutely = "T"
    hourly = "H"
    daily = "D"
    weekly = "W"
    monthly = "M"
    quarterly = "Q"
    yearly = "Y"

    def get_group_value(self, timestamp) -> pd.Period:
        return timestamp.to_period(self.value)


class TimeseriesSplitterParams(Params):
    time_group: TimeGroup = None
    origin: Literal["start", "end"] | datetime = "start"
    ignore_empty_groups: bool = True

    def get_form_class(self) -> type[ParamsForm] | None:
        from .forms import TimeseriesSplitterParamsForm

        return TimeseriesSplitterParamsForm

    @property
    def grouper(self):
        return TimeGrouper(freq=self.time_group.value, origin=self.origin)


class TimeseriesSplitter(BaseStep[pd.DataFrame, dict[pd.Period, pd.DataFrame]]):
    param_schema = TimeseriesSplitterParams
    input_type = pd.DataFrame
    output_type = list[pd.DataFrame]

    def check_context(self, context: StepContext):
        if not ptypes.is_datetime64_any_dtype(context.data.index):
            raise StepError("Dataframe must have a datetime index")

    def run(self, params: TimeseriesSplitterParams, data: pd.DataFrame) -> tuple[list[pd.DataFrame], dict]:
        grouped = data.groupby(params.grouper)
        groups = []
        names = []
        for origin, group in grouped:
            if params.ignore_empty_groups and not len(group):
                continue
            groups.append(group)
            names.append(str(params.time_group.get_group_value(origin)))

        self.log.info(f"Split timeseries data into {len(data)} groups")
        for name, group in zip(names, groups):
            self.log.info(f"Split timeseries data for {name} ({len(group)} rows)")
        return groups, {"names": names, "output_multiple": True}
