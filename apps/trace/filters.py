from collections.abc import Sequence
from typing import ClassVar

from django.urls import reverse

from apps.experiments.filters import (
    get_experiment_filter_options,
    get_filter_context_data,
)
from apps.experiments.models import Experiment
from apps.trace.models import TraceStatus
from apps.web.dynamic_filters.base import ChoiceColumnFilter, ColumnFilter, MultiColumnFilter
from apps.web.dynamic_filters.column_filters import (
    ExperimentFilter,
    ParticipantFilter,
    RemoteIdFilter,
    StatusFilter,
    TimestampFilter,
)


def get_trace_filter_context_data(team):
    span_tags = list(
        team.span_set.filter(tags__is_system_tag=True)
        .values_list("tags__name", flat=True)
        .order_by("tags__name")
        .distinct("tags__name")
    )

    table_url = reverse("trace:table", args=[team.slug])
    context = get_filter_context_data(team, TraceFilter.columns(), "timestamp", table_url, "data-table")
    context.update(
        {
            "df_span_names": list(team.span_set.values_list("name", flat=True).order_by("name").distinct()),
            "df_state_list": TraceStatus.values,
            "df_experiment_list": get_experiment_filter_options(team),
            "df_available_tags": span_tags,
            "df_experiment_versions": Experiment.objects.get_version_names(team),
        }
    )
    return context


class SpanNameFilter(ChoiceColumnFilter):
    query_param = "span_name"
    column: ClassVar[str] = "spans__name"


class SpanTagsFilter(ChoiceColumnFilter):
    query_param = "tags"
    column: ClassVar[str] = "spans__tags__name"


class ExperimentVersionsFilter(ChoiceColumnFilter):
    query_param = "versions"
    column = "experiment__version_number"

    def values_list(self, json_value: str) -> list[str]:
        values = super().values_list(json_value)
        # versions are returned as strings like "v1", "v2", so we need to strip the "v" and convert to int
        return [int(v[1]) for v in values if "v" in v]


class TraceFilter(MultiColumnFilter):
    filters: ClassVar[Sequence[ColumnFilter]] = [
        ParticipantFilter(),
        TimestampFilter(db_column="timestamp", query_param="timestamp"),
        SpanTagsFilter(),
        SpanNameFilter(),
        RemoteIdFilter(),
        ExperimentFilter(),
        ExperimentVersionsFilter(),
        StatusFilter(query_param="status"),
    ]
