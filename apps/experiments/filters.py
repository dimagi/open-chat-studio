import json

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q, Subquery

from apps.annotations.models import CustomTaggedItem
from apps.channels.models import ChannelPlatform
from apps.chat.models import Chat, ChatMessage
from apps.experiments.models import Experiment, SessionStatus
from apps.web.dynamic_filters.base import (
    DATE_RANGE_OPTIONS,
    FIELD_TYPE_FILTERS,
    ColumnFilterMixin,
    DynamicFilter,
    Operators,
)
from apps.web.dynamic_filters.column_filters import (
    ExperimentFilter,
    ParticipantFilter,
    RemoteIdFilter,
    StatusFilter,
    TimestampFilter,
)
from apps.web.dynamic_filters.datastructures import ColumnFilter


def get_experiment_filter_context_data(team, table_url: str, single_experiment=None):
    context = get_filter_context_data(
        team, DynamicExperimentSessionFilter.columns, "last_message", table_url, "sessions-table"
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


class ChatMessageTagsFilter(ColumnFilterMixin):
    query_param = "tags"

    def apply(self, queryset, column_filter: ColumnFilter, timezone=None):
        """Build filter condition for tags"""
        try:
            selected_tags = json.loads(column_filter.value)
            if not selected_tags:
                return queryset

            if column_filter.operator == Operators.ANY_OF:
                chat_tags_condition = Q(chat__tags__name__in=selected_tags)
                message_tags_condition = Q(chat__messages__tags__name__in=selected_tags)
                return queryset.filter(chat_tags_condition | message_tags_condition)

            elif column_filter.operator == Operators.ALL_OF:
                conditions = Q()
                chat_content_type = ContentType.objects.get_for_model(Chat)
                chat_message_content_type = ContentType.objects.get_for_model(ChatMessage)

                for tag in selected_tags:
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

            elif column_filter.operator == Operators.EXCLUDES:
                chat_tags_condition = Q(chat__tags__name__in=selected_tags)
                message_tags_condition = Q(chat__messages__tags__name__in=selected_tags)
                return queryset.exclude(chat_tags_condition | message_tags_condition)

        except json.JSONDecodeError:
            pass
        return queryset


class VersionsFilter(ColumnFilterMixin):
    query_param = "versions"

    def apply(self, queryset, column_filter: ColumnFilter, timezone=None):
        """Build filter condition for versions"""
        try:
            version_strings = json.loads(column_filter.value)
            if not version_strings:
                return queryset

            version_tags = [v for v in version_strings if v]
            if column_filter.operator in [Operators.ANY_OF, Operators.EXCLUDES]:
                tag_exists = [
                    ChatMessage.objects.filter(
                        chat=OuterRef("chat"),
                        tags__name=tag,
                        tags__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
                    ).values("id")
                    for tag in version_tags
                ]
                combined_query = Q()
                for query in tag_exists:
                    combined_query |= Q(Exists(query))

                if column_filter.operator == Operators.EXCLUDES:
                    return queryset.exclude(combined_query)
                else:
                    return queryset.filter(combined_query)

            elif column_filter.operator == Operators.ALL_OF:
                q_objects = Q()
                for tag in version_tags:
                    tag_exists = ChatMessage.objects.filter(
                        chat=OuterRef("chat"),
                        tags__name=tag,
                        tags__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
                    ).values("id")
                    q_objects &= Q(Exists(tag_exists))
                return queryset.filter(q_objects)
        except json.JSONDecodeError:
            pass
        return queryset


class ChannelsFilter(ColumnFilterMixin):
    query_param = "channels"

    def apply(self, queryset, column_filter: ColumnFilter, timezone=None):
        """Build filter condition for channels"""
        try:
            selected_display_names = json.loads(column_filter.value)
            if not selected_display_names:
                return queryset

            display_to_value = {label: val for val, label in ChannelPlatform.choices}
            selected_values = [display_to_value.get(name.strip()) for name in selected_display_names]
            selected_values = [val for val in selected_values if val is not None]
            if not selected_values:
                return queryset

            if column_filter.operator == Operators.ANY_OF:
                return queryset.filter(experiment_channel__platform__in=selected_values)
            elif column_filter.operator == Operators.EXCLUDES:
                return queryset.exclude(experiment_channel__platform__in=selected_values)
        except json.JSONDecodeError:
            pass
        return queryset


class DynamicExperimentSessionFilter(DynamicFilter):
    """Filter for experiment sessions using the new ColumnFilterMixin pattern."""

    columns = [
        "participant",
        "last_message",
        "first_message",
        "tags",
        "versions",
        "channels",
        "experiment",
        "state",
        "remote_id",
    ]

    filters: list[ColumnFilterMixin] = [
        ParticipantFilter(),
        TimestampFilter(accessor="last_message_created_at", query_param="last_message"),
        TimestampFilter(accessor="first_message_created_at", query_param="first_message"),
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
