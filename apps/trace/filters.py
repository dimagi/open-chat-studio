from collections.abc import Sequence
from typing import ClassVar

from django.db.models import Q
from django.urls import reverse

from apps.experiments.filters import (
    get_filter_context_data,
)
from apps.experiments.models import Experiment
from apps.filters.models import FilterSet
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
    context = get_filter_context_data(team, TraceFilter.columns(team), "timestamp", table_url, "data-table")
    context["df_table_type"] = FilterSet.TableType.TRACES
    return context


class SpanNameFilter(ChoiceColumnFilter):
    query_param: str = "span_name"
    column: str = "spans__name"
    label: str = "Span Name"

    def prepare(self, team, **_):
        self.options = list(team.span_set.values_list("name", flat=True).order_by("name").distinct())


class SpanTagsFilter(ChoiceColumnFilter):
    query_param: str = "span_tags"
    column: str = "spans__tags__name"
    label: str = "Span Tags"
    type: str = TYPE_CHOICE

    def prepare(self, team, **_):
        self.options = list(
            team.span_set.filter(tags__is_system_tag=True)
            .values_list("tags__name", flat=True)
            .order_by("tags__name")
            .distinct("tags__name")
        )


class MessageTagsFilter(ChoiceColumnFilter):
    query_param: str = "message_tags"
    label: str = "Message Tags"
    type: str = TYPE_CHOICE

    def prepare(self, team, **_):
        self.options = list(
            team.tag_set.filter(is_system_tag=False).values_list("name", flat=True).order_by("name").distinct()
        )

    def apply_any_of(self, queryset, value, timezone=None):
        input_tags_condition = Q(input_message__tags__name__in=value)
        output_tags_condition = Q(output_message__tags__name__in=value)
        return queryset.filter(input_tags_condition | output_tags_condition).distinct()

    def apply_all_of(self, queryset, value, timezone=None):
        for tag in value:
            input_tags_condition = Q(input_message__tags__name=tag)
            output_tags_condition = Q(output_message__tags__name=tag)
            queryset = queryset.filter(input_tags_condition | output_tags_condition)
        return queryset.distinct()

    def apply_excludes(self, queryset, value, timezone=None):
        input_tags_condition = Q(input_message__tags__name__in=value)
        output_tags_condition = Q(output_message__tags__name__in=value)
        return queryset.exclude(input_tags_condition | output_tags_condition)


class ExperimentVersionsFilter(ChoiceColumnFilter):
    query_param: str = "versions"
    column: str = "experiment_version_number"
    label: str = "Versions"
    type: str = TYPE_CHOICE

    def values_list(self, json_value: str) -> list[str]:
        values = super().values_list(json_value)
        # versions are returned as strings like "v1", "v2", so we need to strip the "v" and convert to int
        return [int(v[1]) for v in values if "v" in v]

    def prepare(self, team, **kwargs):
        self.options = Experiment.objects.get_version_names(team)


class TraceFilter(MultiColumnFilter):
    filters: ClassVar[Sequence[ColumnFilter]] = [
        ParticipantFilter(),
        TimestampFilter(label="Timestamp", column="timestamp", query_param="timestamp"),
        MessageTagsFilter(),
        SpanTagsFilter(),
        SpanNameFilter(),
        RemoteIdFilter(),
        ExperimentFilter(),
        ExperimentVersionsFilter(),
        StatusFilter(query_param="status"),
    ]
