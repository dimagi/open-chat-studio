from collections.abc import Sequence
from typing import ClassVar

from django.contrib.auth import get_user_model
from django.db.models import Exists, OuterRef, QuerySet

from apps.channels.models import ChannelPlatform
from apps.experiments.filters import ChannelsFilter, ChatMessageTagsFilter, VersionsFilter
from apps.web.dynamic_filters.base import ChoiceColumnFilter, ColumnFilter, MultiColumnFilter
from apps.web.dynamic_filters.column_filters import (
    ExperimentFilter,
    ParticipantFilter,
    RemoteIdFilter,
    StatusFilter,
    TimestampFilter,
)

from .models import Annotation, AnnotationItemStatus, AnnotationStatus

User = get_user_model()


class AnnotationItemStatusFilter(ChoiceColumnFilter):
    query_param: str = "status"
    column: str = "status"
    label: str = "Status"
    options: list[str] = [status.label for status in AnnotationItemStatus]
    description: str = "Filter by annotation item status"

    def parse_query_value(self, query_value):
        display_names = self.values_list(query_value)
        if not display_names:
            return None
        label_to_value = {label: val for val, label in AnnotationItemStatus.choices}
        return [label_to_value[name] for name in display_names if name in label_to_value]


class ReviewerFilter(ChoiceColumnFilter):
    query_param: str = "reviewer"
    label: str = "Reviewer"
    description: str = "Filter by reviewer who annotated the item"

    def prepare(self, team, **kwargs):
        reviewers = (
            User.objects.filter(
                annotations__item__queue__team=team,
                annotations__status=AnnotationStatus.SUBMITTED,
            )
            .distinct()
            .values("id", "username", "first_name", "last_name")
        )
        self.options = [
            {"id": str(r["id"]), "label": r["first_name"] + " " + r["last_name"] if r["first_name"] else r["username"]}
            for r in reviewers
        ]

    def parse_query_value(self, query_value):
        values = self.values_list(query_value)
        result = []
        for v in values:
            try:
                result.append(int(v))
            except (ValueError, TypeError):
                continue
        return result

    def apply_any_of(self, queryset, value, timezone=None) -> QuerySet:
        return queryset.filter(
            Exists(
                Annotation.objects.filter(
                    item=OuterRef("pk"),
                    reviewer_id__in=value,
                    status=AnnotationStatus.SUBMITTED,
                )
            )
        )

    def apply_excludes(self, queryset, value, timezone=None) -> QuerySet:
        return queryset.exclude(
            Exists(
                Annotation.objects.filter(
                    item=OuterRef("pk"),
                    reviewer_id__in=value,
                    status=AnnotationStatus.SUBMITTED,
                )
            )
        )


class AnnotationItemFilter(MultiColumnFilter):
    slug: ClassVar[str] = "annotation_item"
    filters: ClassVar[Sequence[ColumnFilter]] = [
        AnnotationItemStatusFilter(),
        ReviewerFilter(),
    ]


class AnnotationSessionFilter(MultiColumnFilter):
    """Session filter for annotation queues that excludes evaluation sessions from the channels filter."""

    slug: ClassVar[str] = "annotation_session"
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
        TimestampFilter(
            label="Message Date",
            column="chat__messages__created_at",
            query_param="message_date",
            description="Filter by message date",
        ),
        ChatMessageTagsFilter(),
        VersionsFilter(),
        ChannelsFilter(exclude_platforms=[ChannelPlatform.EVALUATIONS]),
        ExperimentFilter(),
        StatusFilter(query_param="state"),
        RemoteIdFilter(),
    ]
