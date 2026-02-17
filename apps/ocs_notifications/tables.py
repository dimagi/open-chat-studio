from django.conf import settings
from django.template.loader import get_template
from django_tables2 import columns, tables

from apps.generics.tables import ISOTimeAgoColumn, TimeAgoColumn
from apps.ocs_notifications.models import EventUser, NotificationEvent


class UserNotificationTable(tables.Table):
    # TODO: Rename table
    notification_content = columns.TemplateColumn(
        template_name="ocs_notifications/components/notification_content.html",
        verbose_name="Notification",
        orderable=False,
    )
    level = columns.TemplateColumn(
        template_name="ocs_notifications/components/level_badge.html",
        verbose_name="Level",
        orderable=False,
    )
    read = columns.TemplateColumn(
        template_name="ocs_notifications/components/read_button.html", verbose_name="Read Status", orderable=True
    )
    timestamp = ISOTimeAgoColumn(
        verbose_name="Timestamp",
        accessor="latest_event__created_at",
        orderable=True,
    )

    mute = columns.TemplateColumn(
        template_name="ocs_notifications/components/mute_button.html",
        verbose_name="Mute",
        orderable=False,
    )

    def render_mute(self, record, bound_column, *args, **kwargs):
        template = get_template(bound_column.column.template_name)
        return template.render({"record": record, "is_muted": record.is_muted, "muted_until": record.muted_until})

    class Meta:
        model = EventUser
        fields = (
            "timestamp",
            "notification_content",
            "level",
            "mute",
            "read",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No notifications found."
        attrs = {"td": {"class": "overflow-visible"}}


class NotificationEventTable(tables.Table):
    notification_content = columns.TemplateColumn(
        template_name="ocs_notifications/components/notification_event_content.html",
        verbose_name="Notification",
        orderable=False,
    )
    level = columns.TemplateColumn(
        template_name="ocs_notifications/components/level_badge.html",
        verbose_name="Level",
        orderable=False,
    )
    timestamp = TimeAgoColumn(
        verbose_name="Timestamp",
        accessor="created_at",
        orderable=True,
    )

    class Meta:
        model = NotificationEvent
        fields = (
            "timestamp",
            "notification_content",
            "level",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No notifications found."
        attrs = {"td": {"class": "overflow-visible"}}
