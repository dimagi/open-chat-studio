from collections.abc import Sequence
from typing import ClassVar

from apps.ocs_notifications.models import LevelChoices
from apps.web.dynamic_filters.base import ChoiceColumnFilter, MultiColumnFilter
from apps.web.dynamic_filters.column_filters import TimestampFilter


class ReadFilter(ChoiceColumnFilter):
    """Filter notifications by read status."""

    query_param: str = "read"
    column: str = "read"
    label: str = "Read Status"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.options = [
            {"id": "true", "label": "Read"},
            {"id": "false", "label": "Unread"},
        ]

    def parse_query_value(self, query_value) -> list[bool] | None:
        """Convert string values 'true'/'false' to boolean."""
        values = self.values_list(query_value)
        if not values:
            return None
        return [val.lower() == "true" for val in values]

    def apply_any_of(self, queryset, value, timezone=None):
        """Filter notifications by read status (any of the selected values)."""
        if not value:
            return queryset
        # value is a list of booleans
        return queryset.filter(read__in=value)

    def apply_excludes(self, queryset, value, timezone=None):
        """Exclude notifications with specified read status."""
        if not value:
            return queryset
        return queryset.exclude(read__in=value)


class SeverityLevelFilter(ChoiceColumnFilter):
    """Filter notifications by level/level."""

    query_param: str = "level"
    column: str = "notification__level"
    label: str = "Severity Level"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.options = [{"id": choice[0], "label": choice[1]} for choice in LevelChoices.choices]

    def apply_any_of(self, queryset, value, timezone=None):
        """Filter notifications by level (any of the selected values)."""
        if not value:
            return queryset
        return queryset.filter(notification__level__in=value)

    def apply_excludes(self, queryset, value, timezone=None):
        """Exclude notifications with specified categories."""
        if not value:
            return queryset
        return queryset.exclude(notification__level__in=value)


class UserNotificationFilter(MultiColumnFilter):
    """Filter for user notifications using multiple column filters."""

    filters: ClassVar[Sequence] = [
        ReadFilter(),
        TimestampFilter(label="Notification Date", column="created_at", query_param="notification_date"),
        SeverityLevelFilter(),
    ]
