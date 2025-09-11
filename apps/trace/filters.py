import json

from django.urls import reverse

from apps.experiments.filters import (
    ColumnFilterMixin,
    DynamicFilter,
    ExperimentFilter,
    Operators,
    ParticipantFilter,
    RemoteIdFilter,
    StateFilter,
    TimestampFilter,
    get_experiment_filter_options,
    get_filter_context_data,
)
from apps.trace.models import TraceStatus
from apps.web.dynamic_filters import ColumnFilter, ColumnFilterMixin, DynamicFilter


def get_trace_filter_context_data(team):
    span_tags = list(
        team.span_set.filter(tags__is_system_tag=True)
        .values_list("tags__name", flat=True)
        .order_by("tags__name")
        .distinct("tags__name")
    )

    table_url = reverse("trace:table", args=[team.slug])
    context = get_filter_context_data(team, DynamicTraceFilter.columns, "timestamp", table_url, "data-table")
    context.update(
        {
            "df_span_names": list(team.span_set.values_list("name", flat=True).order_by("name").distinct()),
            "df_state_list": TraceStatus.values,
            "df_experiment_list": get_experiment_filter_options(team),
            "df_available_tags": span_tags,
        }
    )
    return context


class SpanNameFilter(ColumnFilterMixin):
    def apply(self, queryset, column_filter: ColumnFilter, timezone=None):
        try:
            selected_names = json.loads(column_filter.value)
        except json.JSONDecodeError:
            return queryset

        if not selected_names:
            return queryset

        if column_filter.operator == Operators.ANY_OF:
            return queryset.filter(spans__name__in=selected_names)

        elif column_filter.operator == Operators.ALL_OF:
            # Use a special approach for ALL_OF that requires count-based filtering
            return queryset.filter("span_names_all_of", selected_names)

        elif column_filter.operator == Operators.EXCLUDES:
            return queryset.exclude(spans__name__in=selected_names)


class SpanTagsFilter(ColumnFilterMixin):
    query_param = "tags"

    def apply(self, queryset, column_filter: ColumnFilter, timezone=None):
        try:
            selected_tags = json.loads(column_filter.value)
        except json.JSONDecodeError:
            return None

        if not selected_tags:
            return None

        if column_filter.operator == Operators.ANY_OF:
            return queryset.filter(spans__tags__name__in=selected_tags)

        elif column_filter.operator == Operators.ALL_OF:
            # Use a special approach for ALL_OF that requires count-based filtering
            # We'll mark this as needing special handling by returning a special Q object
            return ("tags_all_of", selected_tags)

        elif column_filter.operator == Operators.EXCLUDES:
            return queryset.exclude(spans__tags__name__in=selected_tags)


class DynamicTraceFilter(DynamicFilter):
    columns = [
        "participant",
        "tags",
        "remote_id",
        "timestamp",
        "span_name",
        "experiment",
        "status",
    ]

    filters = [
        ParticipantFilter(),
        TimestampFilter(accessor="timestamp", query_param="timestamp"),
        SpanTagsFilter(),
        RemoteIdFilter(),
        ExperimentFilter(),
        StateFilter(query_param="status"),
    ]
