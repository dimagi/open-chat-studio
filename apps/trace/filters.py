from collections.abc import Sequence
from typing import ClassVar

from django.urls import reverse

from apps.experiments.filters import (
    get_filter_context_data,
)
from apps.web.dynamic_filters.base import TYPE_CHOICE, ChoiceColumnFilter, ColumnFilter, MultiColumnFilter
from apps.web.dynamic_filters.column_filters import (
    ExperimentFilter,
    ParticipantFilter,
    RemoteIdFilter,
    StatusFilter,
    TimestampFilter,
)


def get_trace_filter_context_data(team):
    table_url = reverse("trace:table", args=[team.slug])
    return get_filter_context_data(team, TraceFilter.columns(team), "timestamp", table_url, "data-table")


class SpanNameFilter(ChoiceColumnFilter):
    query_param: str = "span_name"
    column: str = "spans__name"
    label: str = "Spans Name"

    def prepare(self, team, **kwargs):
        self.options = list(team.span_set.values_list("name", flat=True).order_by("name").distinct())


class SpanTagsFilter(ChoiceColumnFilter):
    query_param: str = "tags"
    column: str = "spans__tags__name"
    label: str = "Tags"
    type: str = TYPE_CHOICE

    def prepare(self, team, **kwargs):
        self.options = list(
            team.span_set.filter(tags__is_system_tag=True)
            .values_list("tags__name", flat=True)
            .order_by("tags__name")
            .distinct("tags__name")
        )


class TraceFilter(MultiColumnFilter):
    filters: ClassVar[Sequence[ColumnFilter]] = [
        ParticipantFilter(),
        TimestampFilter(label="Timestamp", column="timestamp", query_param="timestamp"),
        SpanTagsFilter(),
        SpanNameFilter(),
        RemoteIdFilter(),
        ExperimentFilter(),
        StatusFilter(query_param="status"),
    ]
