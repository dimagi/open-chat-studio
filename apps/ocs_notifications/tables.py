from django.conf import settings
from django.template.loader import get_template
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

    def render_mute(self, record, bound_column, *args, **kwargs):
        template = get_template(bound_column.column.template_name)
        return template.render(
            {"record": record, "notification_is_muted": record.notification_is_muted, "muted_until": record.muted_until}
        )

    class Meta:
        model = UserNotification
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
