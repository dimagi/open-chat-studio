from django.conf import settings
from django_tables2 import columns, tables

from apps.generics.tables import TimeAgoColumn
from apps.ocs_notifications.models import UserNotification


class UserNotificationTable(tables.Table):
    notification_content = columns.TemplateColumn(
        template_name="ocs_notifications/components/notification_content.html",
        verbose_name="Notification",
        orderable=False,
    )
    level = columns.TemplateColumn(
        template_name="ocs_notifications/components/level_badge.html",
        accessor="notification__level",
        verbose_name="Level",
        orderable=False,
    )
    read = columns.TemplateColumn(
        template_name="ocs_notifications/components/read_button.html", verbose_name="Read Status", orderable=True
    )
    timestamp = TimeAgoColumn(
        verbose_name="Timestamp",
        accessor="notification__last_event_at",
        orderable=True,
    )
    mute = columns.TemplateColumn(
        template_name="ocs_notifications/components/mute_button.html",
        verbose_name="Mute",
        orderable=False,
    )

    class Meta:
        model = UserNotification
        fields = (
            "notification_content",
            "level",
            "read",
            "timestamp",
            "mute",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No notifications found."
