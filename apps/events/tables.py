import django_tables2 as tables
from django.conf import settings
from django.template.loader import get_template
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext as _

from apps.events.models import EventActionType, StaticTriggerType
from apps.events.utils import truncate_dict_items
from apps.utils.time import seconds_to_human


class ActionsColumn(tables.Column):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def render(self, value, record):  # ty: ignore[invalid-method-override]
        namespace = "chatbots"
        trigger_type = "timeout" if record["type"] == "__timeout__" else "static"
        view_log_url = reverse(
            f"{namespace}:events:{trigger_type}_logs_view",
            kwargs={
                "trigger_id": record["id"],
                "experiment_id": record["experiment_id"],
                "team_slug": record["team_slug"],
            },
        )
        edit_url = reverse(
            f"{namespace}:events:{trigger_type}_event_edit",
            kwargs={
                "trigger_id": record["id"],
                "experiment_id": record["experiment_id"],
                "team_slug": record["team_slug"],
            },
        )
        delete_url = reverse(
            f"{namespace}:events:{trigger_type}_event_delete",
            kwargs={
                "trigger_id": record["id"],
                "experiment_id": record["experiment_id"],
                "team_slug": record["team_slug"],
            },
        )
        toggle_active_flag_url = reverse(
            f"{namespace}:events:{trigger_type}_event_toggle",
            kwargs={
                "trigger_id": record["id"],
                "experiment_id": record["experiment_id"],
                "team_slug": record["team_slug"],
            },
        )
        return get_template("events/events_actions_column_buttons.html").render(
            {
                "view_log_url": view_log_url,
                "edit_url": edit_url,
                "delete_url": delete_url,
                "toggle_active_flag_url": toggle_active_flag_url,
                "event": record,
            }
        )


class ParamsColumn(tables.Column):
    def render(self, value, record):  # ty: ignore[invalid-method-override]
        formatted_items = truncate_dict_items(value)
        items = format_html_join("", "<li><strong>{}</strong>: {}</li>", formatted_items)
        return format_html("<ul>{}</ul>", items)


class EventsTable(tables.Table):
    type = tables.Column(accessor="type", verbose_name="When...")
    action_type = tables.Column(accessor="action__action_type", verbose_name="Then...")
    action_params = ParamsColumn(accessor="action__params", verbose_name="With these parameters...")
    total_num_triggers = tables.Column(accessor="total_num_triggers", verbose_name="Repeat")
    error_count = tables.Column(accessor="failure_count", verbose_name="Error Count")
    action = None

    def __init__(self, *args, **kwargs):
        self.base_columns["actions"] = ActionsColumn(empty_values=())
        super().__init__(*args, **kwargs)

    def render_type(self, value, record):
        if value == "__timeout__":
            return f"No response for {seconds_to_human(record['delay'])}"
        else:
            return StaticTriggerType(value).label

    def render_action_type(self, value):
        return EventActionType(value).label

    def render_total_num_triggers(self, value):
        return f"{value} times"

    class Meta:
        orderable = False
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
            "id": lambda record: f"record-{record['type']}-{record['id']}",
        }
        fields = (
            "type",
            "action_type",
            "action_params",
        )


class SchedulesTable(tables.Table):
    """Scheduled messages, shared by the session-scoped and participant-wide schedule tabs.

    `experiment` (the Chatbot column) is only meaningful when a schedule list aggregates
    across chatbots -- exclude it (`table.exclude = ("experiment",)`) for a single-session
    view, matching the same convention already used for `ChatbotSessionsTable`.
    """

    name = tables.Column(verbose_name="Schedule")
    experiment = tables.Column(verbose_name="Chatbot")
    next_trigger_date = tables.Column(verbose_name="Next run", empty_values=())
    cadence = tables.Column(verbose_name="Cadence", empty_values=())
    status = tables.Column(verbose_name="Status", empty_values=())
    manage = tables.TemplateColumn(
        verbose_name="Manage",
        template_name="events/components/schedule_manage_button.html",
    )

    def render_next_trigger_date(self, record):
        if record.get("is_complete") or record.get("is_cancelled"):
            return "-"
        value = record.get("next_trigger_date")
        if not value:
            return "-"
        return format_html(
            '<time datetime="{}" title="{}">{}</time>',
            value.isoformat(),
            value.isoformat(),
            date_format(value, "DATETIME_FORMAT"),
        )

    def render_cadence(self, record):
        if record.get("repetitions"):
            return _("Every %(frequency)s %(time_period)s, %(repetitions)s times") % {
                "frequency": record["frequency"],
                "time_period": record["time_period"],
                "repetitions": record["repetitions"],
            }
        return _("One-off")

    def render_status(self, record):
        if record.get("is_cancelled"):
            label = _("Cancelled")
        elif record.get("is_complete"):
            label = _("Completed")
        else:
            label = _("Active")
        return format_html('<span class="badge badge-ghost">{}</span>', label)

    class Meta:
        orderable = False
        empty_text = "No schedules."
        row_attrs = {
            "id": lambda record: f"schedule_{record['external_id']}",
            "class": "hover:bg-base-200 transition-colors",
        }
        sequence = ("name", "experiment", "next_trigger_date", "cadence", "status", "manage")
