from collections.abc import Sequence
from typing import ClassVar

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef
from django.urls import reverse

from apps.annotations.models import CustomTaggedItem
from apps.chat.models import ChatMessage
from apps.experiments.filters import (
    get_filter_context_data,
)
from apps.experiments.models import Experiment
from apps.filters.models import FilterSet
from apps.trace.models import TraceStatus
from apps.web.dynamic_filters.base import TYPE_CHOICE, ChoiceColumnFilter, ColumnFilter, MultiColumnFilter
from apps.web.dynamic_filters.column_filters import (
    ExperimentFilter,
    ParticipantFilter,
    RemoteIdFilter,
    TimestampFilter,
)


def get_trace_filter_context_data(team):
    table_url = reverse("trace:table", args=[team.slug])
    context = get_filter_context_data(
        team,
        columns=TraceFilter.columns(team),
        filter_class=TraceFilter,
        table_url=table_url,
        table_container_id="data-table",
        table_type=FilterSet.TableType.TRACES,
    )
    return context


class MessageTagsFilter(ChoiceColumnFilter):
    query_param: str = "message_tags"
    label: str = "Message Tags"
    type: str = TYPE_CHOICE

    def prepare(self, team, **_):
        self.options = list(
            team.tag_set.filter(is_system_tag=False).values_list("name", flat=True).order_by("name").distinct()
        )

    def _input_or_output_message_tag_exists(self, tag_names: list[str]):
        """Build a Q matching traces whose input or output message carries one of ``tag_names``."""
        chat_message_ct = ContentType.objects.get_for_model(ChatMessage)

        def _exists(message_field):
            return Exists(
                CustomTaggedItem.objects.filter(
                    content_type_id=chat_message_ct.id,
                    tag__name__in=tag_names,
                    object_id=OuterRef(message_field),
                )
            )

        return _exists("input_message_id") | _exists("output_message_id")

    def apply_any_of(self, queryset, value, timezone=None):
        return queryset.filter(self._input_or_output_message_tag_exists(value))

    def apply_all_of(self, queryset, value, timezone=None):
        for tag in value:
            queryset = queryset.filter(self._input_or_output_message_tag_exists([tag]))
        return queryset

    def apply_excludes(self, queryset, value, timezone=None):
        return queryset.exclude(self._input_or_output_message_tag_exists(value))


class ExperimentVersionsFilter(ChoiceColumnFilter):
    query_param: str = "versions"
    column: str = "experiment_version_number"
    label: str = "Versions"
    type: str = TYPE_CHOICE

    def values_list(self, json_value: str) -> list[int]:  # ty: ignore[invalid-method-override]
        values = super().values_list(json_value)
        # versions are returned as strings like "v1", "v2", so we need to strip the "v" and convert to int
        return [int(v[1:]) for v in values if "v" in v]

    def prepare(self, team, **kwargs):
        self.options = Experiment.objects.get_version_names(team)


class TraceStatusFilter(ChoiceColumnFilter):
    query_param: str = "status"
    column: str = "status"
    label: str = "Status"
    options: list[str | dict] = [{"id": value, "label": label} for value, label in TraceStatus.choices]
    description: str = "Filter by trace status (e.g. success, error, pending)"


class TraceFilter(MultiColumnFilter):
    slug: ClassVar[str] = "trace"
    date_range_column: ClassVar[str] = "timestamp"
    filters: ClassVar[Sequence[ColumnFilter]] = [
        ParticipantFilter(),
        TimestampFilter(label="Timestamp", column="timestamp", query_param="timestamp"),
        MessageTagsFilter(),
        RemoteIdFilter(),
        ExperimentFilter(),
        ExperimentVersionsFilter(),
        TraceStatusFilter(),
    ]
