from collections.abc import Sequence
from typing import ClassVar

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Subquery

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
    SessionIdFilter,
    SessionStatusFilter,
    TimestampFilter,
)


class MessageTimestampFilter(TimestampFilter):
    """Timestamp filter that traverses ``chat__messages`` without multiplying rows.

    Targets a queryset whose model has a ``chat`` foreign key. Overrides only
    ``_filter_by_lookup`` — date-parsing and the ``apply_*`` methods are inherited
    from :class:`TimestampFilter`.
    """

    def _filter_by_lookup(self, queryset, lookup_suffix: str, value):
        return queryset.filter(
            Exists(ChatMessage.objects.filter(chat_id=OuterRef("chat_id"), **{f"created_at__{lookup_suffix}": value}))
        )


def get_filter_context_data(
    team,
    columns: dict[str, dict],
    filter_class: type[MultiColumnFilter],
    table_url: str,
    table_container_id: str,
    table_type: str,
):
    date_range_column = filter_class.date_range_column
    if date_range_column and date_range_column not in columns:
        raise ValueError("Date range column is not present in list of columns")
    return {
        "df_date_range_options": DATE_RANGE_OPTIONS,
        "df_channel_list": ChannelPlatform.for_filter(team),
        "df_filter_columns": columns,
        "df_date_range_column_name": date_range_column,
        "df_filter_data_source_url": table_url,
        "df_filter_data_source_container_id": table_container_id,
        "df_table_type": table_type,
        "df_filter_slug": filter_class.slug,
    }


class ChatMessageTagsFilter(ChoiceColumnFilter):
    query_param: str = "tags"
    label: str = "Tags"
    type: str = TYPE_CHOICE
    description: str = "Filter by tags on sessions or messages"

    def prepare(self, team, **_):
        self.options = [tag.name for tag in team.tag_set.filter(is_system_tag=False)]

    def _chat_or_message_tag_exists(self, tag_names):
        """Build a Q matching outer rows whose chat or any of its messages carries one of ``tag_names``."""
        chat_ct = ContentType.objects.get_for_model(Chat)
        chat_message_ct = ContentType.objects.get_for_model(ChatMessage)
        chat_tag_exists = Exists(
            CustomTaggedItem.objects.filter(
                object_id=OuterRef("chat_id"),
                content_type_id=chat_ct.id,
                tag__name__in=tag_names,
            )
        )
        # Double OuterRef: the inner Subquery sits inside an Exists, so OuterRef("chat_id")
        # would resolve to the Subquery's parent (CustomTaggedItem). One more OuterRef hop
        # is needed to reach the outermost ExperimentSession queryset's chat_id. Don't
        # collapse to a single OuterRef — that breaks the correlation.
        message_tag_exists = Exists(
            CustomTaggedItem.objects.filter(
                content_type_id=chat_message_ct.id,
                tag__name__in=tag_names,
                object_id__in=Subquery(ChatMessage.objects.filter(chat_id=OuterRef(OuterRef("chat_id"))).values("id")),
            )
        )
        return chat_tag_exists | message_tag_exists

    def apply_any_of(self, queryset, value, timezone=None):
        return queryset.filter(self._chat_or_message_tag_exists(value))

    def apply_all_of(self, queryset, value, timezone=None):
        for tag in value:
            queryset = queryset.filter(self._chat_or_message_tag_exists([tag]))
        return queryset

    def apply_excludes(self, queryset, value, timezone=None):
        return queryset.exclude(self._chat_or_message_tag_exists(value))


def _message_tag_exists(tag_names, category=None):
    """``EXISTS`` matching ``ChatMessage`` rows whose own tags include one of ``tag_names``.

    Used by :class:`MessageTagsFilter` and :class:`MessageVersionsFilter`. Avoids the
    JOIN-multiplication that ``queryset.filter(tags__name__in=...)`` would cause when
    a message carries multiple matching tags — the global ``.distinct()`` was removed
    from :meth:`MultiColumnFilter.apply`, so JOIN-based filters now leak duplicates.
    """
    chat_message_ct = ContentType.objects.get_for_model(ChatMessage)
    qs = CustomTaggedItem.objects.filter(
        content_type_id=chat_message_ct.id,
        tag__name__in=tag_names,
        object_id=OuterRef("pk"),
    )
    if category is not None:
        qs = qs.filter(tag__category=category)
    return Exists(qs)


class MessageTagsFilter(ChoiceColumnFilter):
    """Tag filter for ChatMessage querysets — matches tags on the message itself.

    Distinct from :class:`ChatMessageTagsFilter`, which targets a *session* queryset
    and looks for tags on either the chat or any of its messages.
    """

    query_param: str = "tags"
    label: str = "Tags"
    type: str = TYPE_CHOICE
    description: str = "Filter by tags on messages"

    def prepare(self, team, **_):
        self.options = [tag.name for tag in team.tag_set.filter(is_system_tag=False)]

    def apply_any_of(self, queryset, value, timezone=None):
        return queryset.filter(_message_tag_exists(value))

    def apply_all_of(self, queryset, value, timezone=None):
        for tag in value:
            queryset = queryset.filter(_message_tag_exists([tag]))
        return queryset

    def apply_excludes(self, queryset, value, timezone=None):
        return queryset.exclude(_message_tag_exists(value))


class VersionsFilter(ChoiceColumnFilter):
    query_param: str = "versions"
    label: str = "Versions"
    description: str = "Filter by chatbot version (e.g. v1, v2)"

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


class MessageVersionsFilter(ChoiceColumnFilter):
    """Version filter for ChatMessage querysets — matches the version tag on a message.

    Distinct from :class:`VersionsFilter`, which targets a session queryset and matches
    against the ``experiment_versions`` array column.
    """

    query_param: str = "versions"
    label: str = "Versions"
    description: str = "Filter by message version"

    def prepare(self, team, **kwargs):
        single_experiment = kwargs.get("single_experiment")
        self.options = Experiment.objects.get_version_names(team, working_version=single_experiment)

    def apply_any_of(self, queryset, value, timezone=None):
        return queryset.filter(_message_tag_exists(value, category=Chat.MetadataKeys.EXPERIMENT_VERSION))

    def apply_all_of(self, queryset, value, timezone=None):
        for tag in value:
            queryset = queryset.filter(_message_tag_exists([tag], category=Chat.MetadataKeys.EXPERIMENT_VERSION))
        return queryset

    def apply_excludes(self, queryset, value, timezone=None):
        return queryset.exclude(_message_tag_exists(value, category=Chat.MetadataKeys.EXPERIMENT_VERSION))


class ChannelsFilter(ChoiceColumnFilter):
    query_param: str = "channels"
    label: str = "Channels"
    column: str = "platform"
    description: str = "Filter by messaging platform/channel"
    exclude_platforms: list[str] = []

    def prepare(self, team, **_):
        options = ChannelPlatform.for_filter(team)
        if self.exclude_platforms:
            excluded_labels = {ChannelPlatform(p).label for p in self.exclude_platforms}
            options = [o for o in options if o not in excluded_labels]
        self.options = options  # ty: ignore[invalid-assignment]

    def parse_query_value(self, query_value) -> any:
        selected_display_names = self.values_list(query_value)
        if not selected_display_names:
            return None

        display_to_value = {label: val for val, label in ChannelPlatform.choices}
        selected_values = [display_to_value.get(name.strip()) for name in selected_display_names]
        return [val for val in selected_values if val is not None]


class ExperimentSessionFilter(MultiColumnFilter):
    """Filter for experiment sessions using the new ColumnFilter pattern."""

    slug: ClassVar[str] = "session"
    date_range_column: ClassVar[str] = "last_message"
    filters: ClassVar[Sequence[ColumnFilter]] = [
        ParticipantFilter(),
        TimestampFilter(
            label="Last Message",
            column="last_activity_at",
            query_param="last_message",
            description="Filter by last message time",
        ),
        TimestampFilter(
            label="First Message",
            column="first_activity_at",
            query_param="first_message",
            description="Filter by first message time",
        ),
        MessageTimestampFilter(
            label="Message Date",
            query_param="message_date",
            description="Filter by message date",
        ),
        ChatMessageTagsFilter(),
        VersionsFilter(),
        ChannelsFilter(),
        ExperimentFilter(),
        SessionStatusFilter(query_param="state"),
        RemoteIdFilter(),
        SessionIdFilter(),
    ]


class ChatMessageFilter(MultiColumnFilter):
    """Filter for chat messages using tags, timestamps, and versions."""

    slug: ClassVar[str] = "message"
    date_range_column: ClassVar[str] = "last_message"
    filters: ClassVar[Sequence[ColumnFilter]] = [
        MessageTagsFilter(),
        TimestampFilter(
            label="Message Time",
            column="created_at",
            query_param="last_message",
            description="Filter by message time",
        ),
        MessageVersionsFilter(),
    ]
