from collections.abc import Sequence
from typing import ClassVar

from django.db.models import QuerySet
from django.urls import reverse

from apps.experiments.filters import (
    get_experiment_filter_options,
    get_filter_context_data,
)
from apps.trace.models import TraceStatus
from apps.web.dynamic_filters.base import ColumnFilter, MultiColumnFilter, Operators
from apps.web.dynamic_filters.column_filters import (
    ExperimentFilter,
    ParticipantFilter,
    RemoteIdFilter,
    StatusFilter,
    TimestampFilter,
)
from apps.web.dynamic_filters.datastructures import ColumnFilterData


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
        }
    )
    return context


class SpanNameFilter(ColumnFilter):
    query_param = "span_name"

    def apply_filter(self, queryset, column_filter: ColumnFilterData, timezone=None) -> QuerySet:
        selected_names = self.values_list(column_filter)
        if not selected_names:
            return queryset

        if column_filter.operator == Operators.ANY_OF:
            return queryset.filter(spans__name__in=selected_names)

        elif column_filter.operator == Operators.ALL_OF:
            # Use a special approach for ALL_OF that requires count-based filtering
            for name in selected_names:
                queryset = queryset.filter(spans__name=name)
            return queryset

        elif column_filter.operator == Operators.EXCLUDES:
            return queryset.exclude(spans__name__in=selected_names)


class SpanTagsFilter(ColumnFilter):
    query_param = "tags"

    def apply_filter(self, queryset, column_filter: ColumnFilterData, timezone=None) -> QuerySet:
        selected_tags = self.values_list(column_filter)
        if not selected_tags:
            return queryset

        if column_filter.operator == Operators.ANY_OF:
            return queryset.filter(spans__tags__name__in=selected_tags)

        elif column_filter.operator == Operators.ALL_OF:
            # Use a special approach for ALL_OF that requires count-based filtering
            # We'll mark this as needing special handling by returning a special Q object
            for tag in selected_tags:
                queryset = queryset.filter(spans__tags__name=tag)
            return queryset
        elif column_filter.operator == Operators.EXCLUDES:
            return queryset.exclude(spans__tags__name__in=selected_tags)


class TraceFilter(MultiColumnFilter):
    filters: ClassVar[Sequence[ColumnFilter]] = [
        ParticipantFilter(),
        TimestampFilter(db_column="timestamp", query_param="timestamp"),
        SpanTagsFilter(),
        SpanNameFilter(),
        RemoteIdFilter(),
        ExperimentFilter(),
        StatusFilter(query_param="status"),
    ]
