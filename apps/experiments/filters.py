from collections.abc import Sequence
from typing import ClassVar

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q

from apps.annotations.models import CustomTaggedItem, Tag
from apps.channels.models import ChannelPlatform
from apps.chat.models import MODERATION_STATUS_LABELS, Chat, ChatMessage
from apps.experiments.models import Experiment
from apps.teams.flags import Flags
from apps.web.dynamic_filters.base import (
    DATE_RANGE_OPTIONS,
    TYPE_CHOICE,
    ChoiceColumnFilter,
    ColumnFilter,
    MultiColumnFilter,
)
from apps.web.dynamic_filters.column_filters import (
    AnnotationQueueFilter,
    ExperimentFilter,
    ParticipantFilter,
    RemoteIdFilter,
    SessionIdFilter,
    SessionStatusFilter,
    TimestampFilter,
)


class LastActivityFilter(TimestampFilter):
    """Timestamp filter over the session's last activity, matching the sessions table column.

    ``last_activity_at`` is only written when the participant sends a message, so it is null for
    sessions that never received one (bot-initiated sessions, rows predating the field). Those
    sessions fall back to ``created_at`` here, exactly as
    :attr:`~apps.experiments.models.ExperimentSession.last_activity` does when rendering.
    """

    query_param: str = "last_activity"
    label: str = "Last Activity"
    description: str = (
        "Filter by last activity time: the participant's most recent message, "
        "or the session's creation time if they never sent one"
    )

    def _filter_by_lookup(self, queryset, lookup_suffix: str, value):
        # Two branches rather than a ``Coalesce`` annotation: each side can use the existing
        # column indexes, and the same column can carry two filters (the UI expresses a date
        # range as ``after X`` + ``before Y``) without colliding on an annotation alias.
        # A null ``last_activity_at`` can never satisfy the first branch, so the branches are
        # mutually exclusive — and both target this table, so no row multiplication either.
        return queryset.filter(
            Q(**{f"last_activity_at__{lookup_suffix}": value})
            | Q(last_activity_at__isnull=True, **{f"created_at__{lookup_suffix}": value})
        )


def _session_has_message(**lookups) -> Exists:
    """``EXISTS`` matching outer rows whose chat has a message that satisfies ``lookups``."""
    return Exists(ChatMessage.objects.filter(chat_id=OuterRef("chat_id"), **lookups))


class MessageTimestampFilter(TimestampFilter):
    """Timestamp filter that traverses ``chat__messages`` without multiplying rows.

    Targets a queryset whose model has a ``chat`` foreign key. Overrides only
    ``_filter_by_lookup`` — date-parsing and the ``apply_*`` methods are inherited
    from :class:`TimestampFilter`.
    """

    def _filter_by_lookup(self, queryset, lookup_suffix: str, value):
        return queryset.filter(_session_has_message(**{f"created_at__{lookup_suffix}": value}))


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

    def prepare(self, team, **_) -> "ChatMessageTagsFilter":
        options = [tag.name for tag in team.tag_set.filter(is_system_tag=False)]
        return self.model_copy(update={"options": options})

    def _chat_or_message_tag_exists(self, tag_names):
        """Match outer rows whose chat, or any of the chat's messages, carries one of ``tag_names``.

        Both halves correlate directly on ``chat_id`` at the top level so Postgres can
        precompute each subquery once as a hashed subplan instead of re-running it per
        session row.
        """
        tag_ids = list(Tag.objects.filter(name__in=tag_names).values_list("id", flat=True))
        chat_ct = ContentType.objects.get_for_model(Chat)
        chat_tag_exists = Exists(
            CustomTaggedItem.objects.filter(
                object_id=OuterRef("chat_id"),
                content_type_id=chat_ct.id,
                tag_id__in=tag_ids,
            )
        )
        return chat_tag_exists | _session_has_message(tags__in=tag_ids)

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

    def prepare(self, team, **_) -> "MessageTagsFilter":
        options = [tag.name for tag in team.tag_set.filter(is_system_tag=False)]
        return self.model_copy(update={"options": options})

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

    def prepare(self, team, **kwargs) -> "VersionsFilter":
        single_experiment = kwargs.get("single_experiment")
        options = Experiment.objects.get_version_names(team, working_version=single_experiment)
        return self.model_copy(update={"options": options})

    def _get_version_numbers(self, version_names):
        """Convert version names to numbers removing the 'v' prefix from 'v1'."""
        return [int(name.replace("v", "")) for name in version_names]

    def apply_any_of(self, queryset, value, timezone=None):
        version_numbers = self._get_version_numbers(value)
        return queryset.filter(experiment_versions__overlap=version_numbers)

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

    def prepare(self, team, **kwargs) -> "MessageVersionsFilter":
        single_experiment = kwargs.get("single_experiment")
        options = Experiment.objects.get_version_names(team, working_version=single_experiment)
        return self.model_copy(update={"options": options})

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

    def prepare(self, team, **_) -> "ChannelsFilter":
        options = ChannelPlatform.for_filter(team)
        if self.exclude_platforms:
            excluded_labels = {ChannelPlatform(p).label for p in self.exclude_platforms}
            options = [o for o in options if o not in excluded_labels]
        return self.model_copy(update={"options": options})

    def parse_query_value(self, query_value) -> any:
        selected_display_names = self.values_list(query_value)
        if not selected_display_names:
            return None

        display_to_value = {label: val for val, label in ChannelPlatform.choices}
        selected_values = [display_to_value.get(name.strip()) for name in selected_display_names]
        return [val for val in selected_values if val is not None]


class ModerationStatusFilter(ChoiceColumnFilter):
    """Session filter on the moderation status of the session's messages."""

    query_param: str = "moderation_status"
    label: str = "Moderation Status"
    description: str = "Filter by moderation status. A session matches when any of its messages has the status."
    options: list[str | dict] = [
        {"id": status.value, "label": label} for status, label in MODERATION_STATUS_LABELS.items()
    ]

    def apply_any_of(self, queryset, value, timezone=None):
        return queryset.filter(_session_has_message(moderation_status__in=value))

    def apply_excludes(self, queryset, value, timezone=None):
        return queryset.exclude(_session_has_message(moderation_status__in=value))

    def apply_all_of(self, queryset, value, timezone=None):
        """The filter does not offer "all of", so a query parameter that uses it has no effect."""
        return queryset


class RuleHitFilter(ChoiceColumnFilter):
    """Session filter on whether an abuse rule fired on any of the session's messages."""

    RULE_HIT: ClassVar[str] = "true"
    NO_RULE_HIT: ClassVar[str] = "false"

    query_param: str = "rule_hit"
    label: str = "Rule Hit"
    description: str = "Filter by whether an abuse rule fired on any message in the session"
    options: list[str | dict] = [
        {"id": RULE_HIT, "label": "A rule fired"},
        {"id": NO_RULE_HIT, "label": "No rule fired"},
    ]

    def _matching(self, value) -> Exists | None:
        """The condition for the selected options, or None when they select every session (or none)."""
        any_rule_hit = _session_has_message(moderation_detail__rules__0__isnull=False)
        selected = set(value) & {self.RULE_HIT, self.NO_RULE_HIT}
        if selected == {self.RULE_HIT}:
            return any_rule_hit
        if selected == {self.NO_RULE_HIT}:
            return ~any_rule_hit
        return None

    def apply_any_of(self, queryset, value, timezone=None):
        condition = self._matching(value)
        return queryset if condition is None else queryset.filter(condition)

    def apply_excludes(self, queryset, value, timezone=None):
        condition = self._matching(value)
        return queryset if condition is None else queryset.exclude(condition)

    def apply_all_of(self, queryset, value, timezone=None):
        """The filter does not offer "all of", so a query parameter that uses it has no effect."""
        return queryset


class ExperimentSessionFilter(MultiColumnFilter):
    """Filter for experiment sessions using the new ColumnFilter pattern."""

    slug: ClassVar[str] = "session"
    # The quick date-range dropdown targets last activity rather than the participant's last
    # message, so "Last 7 Days" keeps sessions the participant never replied to.
    date_range_column: ClassVar[str] = "last_activity"
    filters: ClassVar[Sequence[ColumnFilter]] = [
        ParticipantFilter(),
        LastActivityFilter(),
        TimestampFilter(
            label="Last Participant Message",
            column="last_activity_at",
            query_param="last_message",
            description="Filter by the time of the participant's most recent message",
        ),
        TimestampFilter(
            label="First Participant Message",
            column="first_activity_at",
            query_param="first_message",
            description="Filter by the time of the participant's first message",
        ),
        MessageTimestampFilter(
            label="Message Date",
            query_param="message_date",
            description="Filter by message date",
        ),
        ChatMessageTagsFilter(),
        AnnotationQueueFilter(required_flag=Flags.HUMAN_ANNOTATIONS.slug),
        VersionsFilter(),
        ChannelsFilter(),
        ExperimentFilter(),
        SessionStatusFilter(query_param="state"),
        RemoteIdFilter(),
        SessionIdFilter(),
        ModerationStatusFilter(required_flag=Flags.ABUSE_DETECTION.slug),
        RuleHitFilter(required_flag=Flags.ABUSE_DETECTION.slug),
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
