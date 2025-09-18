import operator
from collections.abc import Sequence
from typing import ClassVar

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q, Subquery

from apps.annotations.models import CustomTaggedItem
from apps.channels.models import ChannelPlatform
from apps.chat.models import Chat, ChatMessage
from apps.experiments.models import Experiment, SessionStatus
from apps.web.dynamic_filters.base import (
    DATE_RANGE_OPTIONS,
    FIELD_TYPE_FILTERS,
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


def get_experiment_filter_context_data(team, table_url: str, single_experiment=None):
    context = get_filter_context_data(
        team, ExperimentSessionFilter.columns(), "last_message", table_url, "sessions-table"
    )
    context.update(
        {
            "df_state_list": SessionStatus.for_chatbots(),
            "df_available_tags": [tag.name for tag in team.tag_set.filter(is_system_tag=False)],
        }
    )

    context["df_experiment_versions"] = Experiment.objects.get_version_names(team, working_version=single_experiment)
    if not single_experiment:
        context["df_experiment_list"] = get_experiment_filter_options(team)
    return context


def get_filter_context_data(team, columns, date_range_column: str, table_url: str, table_container_id: str):
    return {
        "df_field_type_filters": FIELD_TYPE_FILTERS,
        "df_date_range_options": DATE_RANGE_OPTIONS,
        "df_channel_list": ChannelPlatform.for_filter(team),
        "df_filter_columns": columns,
        "df_date_range_column_name": date_range_column,
        "df_filter_data_source_url": table_url,
        "df_filter_data_source_container_id": table_container_id,
    }


def get_experiment_filter_options(team):
    experiments = Experiment.objects.working_versions_queryset().filter(team=team).values("id", "name").order_by("name")
    return [{"id": exp["id"], "label": exp["name"]} for exp in experiments]


class ChatMessageTagsFilter(ChoiceColumnFilter):
    query_param: str = "tags"

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
    column: str = "experiment_channel__platform"

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
        TimestampFilter(column="last_message_created_at", query_param="last_message"),
        TimestampFilter(column="first_message_created_at", query_param="first_message"),
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
