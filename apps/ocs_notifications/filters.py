from collections.abc import Sequence
from typing import Any, ClassVar

from apps.ocs_notifications.models import LevelChoices
from apps.web.dynamic_filters.base import ChoiceColumnFilter, MultiColumnFilter
from apps.web.dynamic_filters.column_filters import TimestampFilter


class ReadFilter(ChoiceColumnFilter):
    """Filter notifications by read status."""

    query_param: str = "read"
    column: str = "read"
    label: str = "Read Status"
    options: list[dict[str, Any]] = [
        {"id": "true", "label": "Read"},
        {"id": "false", "label": "Unread"},
    ]

    def parse_query_value(self, query_value) -> list[bool] | None:
        """Convert string values 'true'/'false' to boolean."""
        values = self.values_list(query_value)
        if not values:
            return None
        return [val.lower() == "true" for val in values]


class MuteFilter(ChoiceColumnFilter):
    query_param: str = "muted"
    # The column is annotated on the queryset in the view, so we can filter on it directly
    column: str = "notification_is_muted"
    label: str = "Muted"
    options: list[dict[str, Any]] = [
        {"id": "true", "label": "Muted"},
        {"id": "false", "label": "Not Muted"},
    ]

    def parse_query_value(self, query_value) -> list[bool] | None:
        """Convert string values 'true'/'false' to boolean."""
        values = self.values_list(query_value)
        if not values:
            return None
        return [val.lower() == "true" for val in values]


class SeverityLevelFilter(ChoiceColumnFilter):
    """Filter notifications by level/level."""

    query_param: str = "level"
    column: str = "notification__level"
    label: str = "Severity Level"
    options: list[str | dict[str, Any]] = [{"id": choice[0], "label": choice[1]} for choice in LevelChoices.choices]


class UserNotificationFilter(MultiColumnFilter):
    """Filter for user notifications using multiple column filters."""

    filters: ClassVar[Sequence] = [
        ReadFilter(),
        TimestampFilter(label="Notification Date", column="created_at", query_param="notification_date"),
        SeverityLevelFilter(),
        MuteFilter(),
    ]
