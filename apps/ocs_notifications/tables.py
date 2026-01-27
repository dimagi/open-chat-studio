from django.conf import settings
from django_tables2 import columns, tables

from apps.ocs_notifications.models import UserNotification


class UserNotificationTable(tables.Table):
    notification_title = columns.Column(accessor="notification__title", verbose_name="Title")
    notification_message = columns.Column(accessor="notification__message", verbose_name="Message")
    notification_category = columns.Column(accessor="notification__category", verbose_name="Category")
    read = columns.BooleanColumn(verbose_name="Read")

    class Meta:
        model = UserNotification
        fields = (
            "notification_title",
            "notification_message",
            "notification_category",
            "read",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No notifications found."
