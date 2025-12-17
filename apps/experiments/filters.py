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
    team, columns: dict[str, dict], date_range_column: str, table_url: str, table_container_id: str, table_type: str
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
        "df_table_type": table_type,
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


class MessageTagsFilter(ChatMessageTagsFilter):
    """Simple tags filter for messages - works directly on message tags."""

    def apply_any_of(self, queryset, value, timezone=None):
        return queryset.filter(tags__name__in=value)

    def apply_all_of(self, queryset, value, timezone=None):
        for tag in value:
            queryset = queryset.filter(tags__name=tag)
        return queryset

    def apply_excludes(self, queryset, value, timezone=None):
        return queryset.exclude(tags__name__in=value)


class VersionsFilter(ChoiceColumnFilter):
    query_param: str = "versions"
    label: str = "Versions"

    def prepare(self, team, **kwargs):
        single_experiment = kwargs.get("single_experiment")
        self.options = Experiment.objects.get_version_names(team, working_version=single_experiment)

    def _get_version_numbers(self, version_names):
        """Convert version names to numbers removing the 'v' prefix from 'v1'."""
        return [int(name.replace("v", "")) for name in version_names]

    def apply_any_of(self, queryset, value, timezone=None):
        version_numbers = self._get_version_numbers(value)
        qs = queryset.filter(experiment_versions__overlap=version_numbers)
        return qs

    def apply_excludes(self, queryset, value, timezone=None):
        version_numbers = self._get_version_numbers(value)
        return queryset.exclude(experiment_versions__overlap=version_numbers)

    def apply_all_of(self, queryset, value, timezone=None):
        version_numbers = self._get_version_numbers(value)
        return queryset.filter(experiment_versions__contains=version_numbers)


class MessageVersionsFilter(VersionsFilter):
    """Versions filter for messages - works directly on message version tags."""

    def apply_any_of(self, queryset, value, timezone=None):
        return queryset.filter(tags__name__in=value, tags__category=Chat.MetadataKeys.EXPERIMENT_VERSION)

    def apply_all_of(self, queryset, value, timezone=None):
        for tag in value:
            queryset = queryset.filter(tags__name=tag, tags__category=Chat.MetadataKeys.EXPERIMENT_VERSION)
        return queryset

    def apply_excludes(self, queryset, value, timezone=None):
        return queryset.exclude(tags__name__in=value, tags__category=Chat.MetadataKeys.EXPERIMENT_VERSION)


class ChannelsFilter(ChoiceColumnFilter):
    query_param: str = "channels"
    label: str = "Channels"
    column: str = "platform"

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
        TimestampFilter(label="Last Message", column="last_activity_at", query_param="last_message"),
        TimestampFilter(label="First Message", column="first_message_created_at", query_param="first_message"),
        TimestampFilter(label="Message Date", column="chat__messages__created_at", query_param="message_date"),
        ChatMessageTagsFilter(),
        VersionsFilter(),
        ChannelsFilter(),
        ExperimentFilter(),
        StatusFilter(query_param="state"),
        RemoteIdFilter(),
    ]

    def prepare_queryset(self, queryset):
        """Prepare the queryset by annotating with first message timestamp."""
        return queryset.annotate_with_first_message_created_at()  # ok


class ChatMessageFilter(MultiColumnFilter):
    """Filter for chat messages using tags, timestamps, and versions."""

    filters: ClassVar[Sequence[ColumnFilter]] = [
        MessageTagsFilter(),
        TimestampFilter(label="Message Time", column="created_at", query_param="last_message"),
        MessageVersionsFilter(),
    ]
