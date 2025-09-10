import json
from datetime import datetime, timedelta
from enum import StrEnum

import pytz
from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q, QuerySet, Subquery

from apps.annotations.models import CustomTaggedItem
from apps.channels.models import ChannelPlatform
from apps.chat.models import Chat, ChatMessage
from apps.experiments.models import Experiment, SessionStatus


class Operators(StrEnum):
    """Enum for filter operators used in dynamic filters."""

    EQUALS = "equals"
    CONTAINS = "contains"
    DOES_NOT_CONTAIN = "does not contain"
    STARTS_WITH = "starts with"
    ENDS_WITH = "ends with"
    ON = "on"
    BEFORE = "before"
    AFTER = "after"
    ANY_OF = "any of"
    ALL_OF = "all of"
    EXCLUDES = "excludes"
    RANGE = "range"


FIELD_TYPE_FILTERS = {
    "string": [
        Operators.EQUALS,
        Operators.CONTAINS,
        Operators.DOES_NOT_CONTAIN,
        Operators.STARTS_WITH,
        Operators.ENDS_WITH,
        Operators.ANY_OF,
    ],
    "timestamp": [Operators.ON, Operators.BEFORE, Operators.AFTER, Operators.RANGE],
    "choice": [Operators.ANY_OF, Operators.ALL_OF, Operators.EXCLUDES],
}

DATE_RANGE_OPTIONS = [
    {"label": "Last 1 Hour", "value": "1h"},
    {"label": "Last 1 Day", "value": "1d"},
    {"label": "Last 7 Days", "value": "7d"},
    {"label": "Last 15 Days", "value": "15d"},
    {"label": "Last 30 Days", "value": "30d"},
]


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


# TODO: Add Readme on how to use the filter component
class DynamicFilter:
    columns: list = []

    def __init__(self, queryset, parsed_params, timezone):
        self.queryset = queryset
        self.parsed_params = parsed_params
        self.timezone = timezone

    def prepare_queryset(self) -> QuerySet:
        return self.queryset

    def apply(self):
        queryset = self.prepare_queryset()
        param_source = self.parsed_params
        filter_conditions = Q()
        filter_applied = False

        for i in range(30):
            filter_column = param_source.get(f"filter_{i}_column")
            if filter_column not in self.columns:
                continue
            filter_operator = param_source.get(f"filter_{i}_operator")
            filter_value = param_source.get(f"filter_{i}_value")

            if not all([filter_column, filter_operator, filter_value]):
                break

            filter_column = filter_column[0] if isinstance(filter_column, list) else filter_column
            filter_operator = filter_operator[0] if isinstance(filter_operator, list) else filter_operator
            filter_value = filter_value[0] if isinstance(filter_value, list) else filter_value

            condition = self._build_filter_condition(filter_column, filter_operator, filter_value)
            if condition:
                filter_conditions &= condition
                filter_applied = True

        if filter_applied:
            queryset = queryset.filter(filter_conditions).distinct()

        return queryset

    def build_participant_filter(self, operator, value):
        """Build filter condition for participant"""
        if operator == Operators.EQUALS:
            return Q(participant__identifier=value)
        elif operator == Operators.CONTAINS:
            return Q(participant__identifier__icontains=value)
        elif operator == Operators.DOES_NOT_CONTAIN:
            return ~Q(participant__identifier__icontains=value)
        elif operator == Operators.STARTS_WITH:
            return Q(participant__identifier__istartswith=value)
        elif operator == Operators.ENDS_WITH:
            return Q(participant__identifier__iendswith=value)
        elif operator == Operators.ANY_OF:
            value = json.loads(value)
            return Q(participant__identifier__in=value)
        return None

    def build_timestamp_filter(self, operator, value, field=None, timezone=None):
        """Build filter condition for timestamp, supporting date and relative ranges like '1h', '7d'.
        For 1d 24h are subtracted i.e sessions in the range of 24h are shown not based on the date"""

        if not value or not field:
            return None

        try:
            client_tz = pytz.timezone(timezone) if timezone else pytz.UTC
            now_client = datetime.now(client_tz)
            # Handle 'range' operator with relative time (e.g., '1h', '7d')
            if operator == Operators.RANGE:
                if not value.endswith(("h", "d", "m")):
                    return None
                num = int(value[:-1])
                unit = value[-1]

                if unit == "h":
                    delta = timedelta(hours=num)
                elif unit == "d":
                    delta = timedelta(days=num)
                elif unit == "m":
                    delta = timedelta(minutes=num)

                range_starting_client_time = now_client - delta
                range_starting_utc_time = range_starting_client_time.astimezone(pytz.UTC)
                return Q(**{f"{field}__gte": range_starting_utc_time})

            else:
                # No need to convert the date as it is in client's timezone
                date_value = datetime.fromisoformat(value)
                if operator == Operators.ON:
                    return Q(**{f"{field}__date": date_value})
                elif operator == Operators.BEFORE:
                    return Q(**{f"{field}__date__lt": date_value})
                elif operator == Operators.AFTER:
                    return Q(**{f"{field}__date__gt": date_value})
        except (ValueError, TypeError, pytz.UnknownTimeZoneError):
            pass
        return None

    def _build_filter_condition(self, column, operator, value):
        if not value:
            # Ignore columns that are unknown to this filter
            return None
        return self.build_filter_condition(column, operator, value)

    def build_filter_condition(self, column, operator, value):
        raise NotImplementedError("This method should be overridden in subclasses")

    def build_tags_filter(self, operator, value):
        raise NotImplementedError("Tags filter is not implemented")

    def build_versions_filter(self, operator, value):
        raise NotImplementedError("Versions filter is not implemented")

    def build_channels_filter(self, operator, value):
        raise NotImplementedError("Channels filter is not implemented")

    def build_experiment_filter(self, operator, value):
        try:
            selected_experiment_ids = json.loads(value)
            if not selected_experiment_ids:
                return None
            # Convert to integers if they're strings
            experiment_ids = []
            for exp_id in selected_experiment_ids:
                try:
                    experiment_ids.append(int(exp_id))
                except (ValueError, TypeError):
                    continue

            if not experiment_ids:
                return None

            if operator == Operators.ANY_OF:
                return Q(experiment_id__in=experiment_ids)
            elif operator == Operators.EXCLUDES:
                return ~Q(experiment_id__in=experiment_ids)
        except json.JSONDecodeError:
            pass
        return None

    def build_state_filter(self, operator, value):
        try:
            selected_values = json.loads(value)
        except json.JSONDecodeError:
            return None

        if not selected_values:
            return None

        if operator == Operators.ANY_OF:
            return Q(status__in=selected_values)

        elif operator == Operators.EXCLUDES:
            return ~Q(status__in=selected_values)

        return None

    def build_remote_id_filter(self, operator, value):
        try:
            selected_values = json.loads(value)
        except json.JSONDecodeError:
            return None

        if not selected_values:
            return None
        if operator == Operators.ANY_OF:
            return Q(participant__remote_id__in=selected_values)
        elif operator == Operators.EXCLUDES:
            return ~Q(participant__remote_id__in=selected_values)

        return None


class DynamicExperimentSessionFilter(DynamicFilter):
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

    def build_filter_condition(self, column, operator, value):
        if column == "participant":
            return self.build_participant_filter(operator, value)
        elif column == "last_message":
            return self.build_timestamp_filter(operator, value, "last_message_created_at", self.timezone)
        elif column == "first_message":
            return self.build_timestamp_filter(operator, value, "first_message_created_at", self.timezone)
        elif column == "tags":
            return self.build_tags_filter(operator, value)
        elif column == "versions":
            return self.build_versions_filter(operator, value)
        elif column == "channels":
            return self.build_channels_filter(operator, value)
        elif column == "experiment":
            return self.build_experiment_filter(operator, value)
        elif column == "state":
            return self.build_state_filter(operator, value)
        elif column == "remote_id":
            return self.build_remote_id_filter(operator, value)
        return None

    def prepare_queryset(self):
        """Prepare the queryset by annotating with first and last message timestamps."""
        queryset = self.queryset
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

    def build_tags_filter(self, operator, value):
        try:
            selected_tags = json.loads(value)
            if not selected_tags:
                return None
            if operator == Operators.ANY_OF:
                chat_tags_condition = Q(chat__tags__name__in=selected_tags)
                message_tags_condition = Q(chat__messages__tags__name__in=selected_tags)
                return chat_tags_condition | message_tags_condition

            elif operator == Operators.ALL_OF:
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
                return conditions

            elif operator == Operators.EXCLUDES:
                chat_tags_condition = Q(chat__tags__name__in=selected_tags)
                message_tags_condition = Q(chat__messages__tags__name__in=selected_tags)
                return ~(chat_tags_condition | message_tags_condition)

        except json.JSONDecodeError:
            pass
        return None

    def build_versions_filter(self, operator, value):
        try:
            version_strings = json.loads(value)
            if not version_strings:
                return None
            version_tags = [v for v in version_strings if v]
            if operator in [Operators.ANY_OF, Operators.EXCLUDES]:
                tag_exists = [
                    ChatMessage.objects.filter(
                        chat=OuterRef("chat"),
                        tags__name__startswith=tag,
                        tags__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
                    ).values("id")
                    for tag in version_tags
                ]
                combined_query = Q()
                for query in tag_exists:
                    combined_query |= Q(Exists(query))

                return ~combined_query if operator == Operators.EXCLUDES else combined_query

            elif operator == Operators.ALL_OF:
                q_objects = Q()
                for tag in version_tags:
                    tag_exists = ChatMessage.objects.filter(
                        chat=OuterRef("chat"),
                        tags__name__startswith=tag,
                        tags__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
                    ).values("id")
                    q_objects &= Q(Exists(tag_exists))
                return q_objects
        except json.JSONDecodeError:
            pass
        return None

    def build_channels_filter(self, operator, value):
        try:
            selected_display_names = json.loads(value)
            if not selected_display_names:
                return None

            display_to_value = {label: val for val, label in ChannelPlatform.choices}
            selected_values = [display_to_value.get(name.strip()) for name in selected_display_names]
            selected_values = [val for val in selected_values if val is not None]
            if not selected_values:
                return None
            if operator == Operators.ANY_OF:
                return Q(experiment_channel__platform__in=selected_values)
            elif operator == Operators.ALL_OF:
                conditions = Q()
                for channel in selected_values:
                    conditions &= Q(experiment_channel__platform__iexact=channel)
                return conditions
            elif operator == Operators.EXCLUDES:
                return ~Q(experiment_channel__platform__in=selected_values)
        except json.JSONDecodeError:
            pass
        return None
