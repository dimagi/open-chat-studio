import operator
from collections.abc import Sequence
from typing import ClassVar

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q, Subquery

from apps.annotations.models import CustomTaggedItem
from apps.channels.models import ChannelPlatform
from apps.chat.models import Chat, ChatMessage
from apps.experiments.models import Experiment
from apps.web.dynamic_filters.base import (
    DATE_RANGE_OPTIONS,
    TYPE_CHOICE,
    ChoiceColumnFilter,
    ColumnFilter,
    MultiColumnFilter,
)
from apps.web.dynamic_filters.column_filters import (
    ExperimentFilter,
    ParticipantFilter,
    RemoteIdFilter,
    StatusFilter,
    TimestampFilter,
)


def get_filter_context_data(
    team, columns: dict[str, ColumnFilter], date_range_column: str, table_url: str, table_container_id: str
):
    if date_range_column not in columns:
        raise ValueError("Date range column is not present in list of columns")
    return {
        "df_date_range_options": DATE_RANGE_OPTIONS,
        "df_channel_list": ChannelPlatform.for_filter(team),
        "df_filter_columns": columns,
        "df_date_range_column_name": date_range_column,
        "df_filter_data_source_url": table_url,
        "df_filter_data_source_container_id": table_container_id,
    }


class ChatMessageTagsFilter(ChoiceColumnFilter):
    query_param: str = "tags"
    label: str = "Tags"
    type: str = TYPE_CHOICE

    def prepare(self, team, **_):
        self.options = [tag.name for tag in team.tag_set.filter(is_system_tag=False)]

    def apply_any_of(self, queryset, value, timezone=None):
        chat_tags_condition = Q(chat__tags__name__in=value)
        message_tags_condition = Q(chat__messages__tags__name__in=value)
        return queryset.filter(chat_tags_condition | message_tags_condition)

    def apply_all_of(self, queryset, value, timezone=None):
        conditions = Q()
        chat_content_type = ContentType.objects.get_for_model(Chat)
        chat_message_content_type = ContentType.objects.get_for_model(ChatMessage)

        for tag in value:
            chat_tag_exists = Exists(
                CustomTaggedItem.objects.filter(
                    object_id=OuterRef("chat_id"),
                    content_type_id=chat_content_type.id,
                    tag__name=tag,
                )
            )
            message_tag_exists = Exists(
                CustomTaggedItem.objects.filter(
                    content_type_id=chat_message_content_type.id,
                    tag__name=tag,
                    object_id__in=Subquery(
                        ChatMessage.objects.filter(chat_id=OuterRef(OuterRef("chat_id"))).values("id")
                    ),
                )
            )
            conditions &= chat_tag_exists | message_tag_exists
        return queryset.filter(conditions)

    def apply_excludes(self, queryset, value, timezone=None):
        chat_tags_condition = Q(chat__tags__name__in=value)
        message_tags_condition = Q(chat__messages__tags__name__in=value)
        return queryset.exclude(chat_tags_condition | message_tags_condition)


class VersionsFilter(ChoiceColumnFilter):
    query_param: str = "versions"
    label: str = "Versions"

    def prepare(self, team, **kwargs):
        single_experiment = kwargs.get("single_experiment")
        self.options = Experiment.objects.get_version_names(team, working_version=single_experiment)

    def _get_messages_queryset(self, tags, operator):
        combined_query = Q()
        for tag in tags:
            queryset = ChatMessage.objects.filter(
                chat=OuterRef("chat"),
                tags__name=tag,
                tags__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
            ).values("id")
            combined_query = operator(combined_query, Q(Exists(queryset)))
        return combined_query

    def apply_any_of(self, queryset, value, timezone=None):
        combined_query = self._get_messages_queryset(value, operator.or_)
        return queryset.filter(combined_query)

    def apply_excludes(self, queryset, value, timezone=None):
        combined_query = self._get_messages_queryset(value, operator.or_)
        return queryset.exclude(combined_query)

    def apply_all_of(self, queryset, value, timezone=None):
        combined_query = self._get_messages_queryset(value, operator.and_)
        return queryset.filter(combined_query)


class ChannelsFilter(ChoiceColumnFilter):
    query_param: str = "channels"
    label: str = "Channels"
    column: str = "experiment_channel__platform"

    def prepare(self, team, **_):
        self.options = ChannelPlatform.for_filter(team)

    def parse_query_value(self, query_value) -> any:
        selected_display_names = self.values_list(query_value)
        if not selected_display_names:
            return None

        display_to_value = {label: val for val, label in ChannelPlatform.choices}
        selected_values = [display_to_value.get(name.strip()) for name in selected_display_names]
        return [val for val in selected_values if val is not None]


class ExperimentSessionFilter(MultiColumnFilter):
    """Filter for experiment sessions using the new ColumnFilter pattern."""

    filters: ClassVar[Sequence[ColumnFilter]] = [
        ParticipantFilter(),
        TimestampFilter(label="Last Message", column="last_message_created_at", query_param="last_message"),
        TimestampFilter(label="First Message", column="first_message_created_at", query_param="first_message"),
        ChatMessageTagsFilter(),
        VersionsFilter(),
        ChannelsFilter(),
        ExperimentFilter(),
        StatusFilter(query_param="state"),
        RemoteIdFilter(),
    ]

    def prepare_queryset(self, queryset):
        """Prepare the queryset by annotating with first and last message timestamps."""
        first_message_subquery = (
            ChatMessage.objects.filter(
                chat__experiment_session=OuterRef("pk"),
            )
            .order_by("created_at")
            .values("created_at")[:1]
        )

        last_message_subquery = (
            ChatMessage.objects.filter(
                chat__experiment_session=OuterRef("pk"),
            )
            .order_by("-created_at")
            .values("created_at")[:1]
        )

        queryset = queryset.annotate(first_message_created_at=Subquery(first_message_subquery))
        queryset = queryset.annotate(last_message_created_at=Subquery(last_message_subquery))
        return queryset
