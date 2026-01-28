from collections.abc import Sequence
from typing import ClassVar

from apps.ocs_notifications.models import CategoryChoices
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


class StatusFilter(ChoiceColumnFilter):
    """Filter notifications by category/status."""

    query_param: str = "status"
    column: str = "notification__category"
    label: str = "Status"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.options = [{"id": choice[0], "label": choice[1]} for choice in CategoryChoices.choices]

    def apply_any_of(self, queryset, value, timezone=None):
        """Filter notifications by category (any of the selected values)."""
        if not value:
            return queryset
        return queryset.filter(notification__category__in=value)

    def apply_excludes(self, queryset, value, timezone=None):
        """Exclude notifications with specified categories."""
        if not value:
            return queryset
        return queryset.exclude(notification__category__in=value)


class UserNotificationFilter(MultiColumnFilter):
    """Filter for user notifications using multiple column filters."""

    filters: ClassVar[Sequence] = [
        ReadFilter(),
        TimestampFilter(label="Notification Date", column="created_at", query_param="notification_date"),
        StatusFilter(),
    ]
