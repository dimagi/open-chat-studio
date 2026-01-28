from django.conf import settings
from django_tables2 import columns, tables

from apps.ocs_notifications.models import UserNotification


class UserNotificationTable(tables.Table):
    notification_content = columns.TemplateColumn(
        template_name="ocs_notifications/components/notification_content.html",
        verbose_name="Notification",
        orderable=False,
    )
    category = columns.TemplateColumn(
        template_name="ocs_notifications/components/category_badge.html",
        accessor="notification__category",
        verbose_name="Category",
        orderable=False,
    )
    read = columns.TemplateColumn(
        template_name="ocs_notifications/components/read_button.html", verbose_name="Read Status", orderable=False
    )

    class Meta:
        model = UserNotification
        fields = (
            "notification_content",
            "category",
            "read",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No notifications found."
