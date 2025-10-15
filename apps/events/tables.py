import django_tables2 as tables
from django.conf import settings
from django.template.loader import get_template
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from apps.events.models import EventActionType, StaticTriggerType
from apps.events.utils import truncate_dict_items
from apps.utils.time import seconds_to_human


class ActionsColumn(tables.Column):
    def __init__(self, origin=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.origin = origin

    def render(self, value, record):
        namespace = "chatbots" if self.origin == "chatbots" else "experiments"
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
    def render(self, value, record):
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

    def __init__(self, *args, origin=None, **kwargs):
        self.base_columns["actions"] = ActionsColumn(origin=origin, empty_values=())
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
