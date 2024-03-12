import django_tables2 as tables
from django.conf import settings

from apps.events.models import EventActionType, StaticTriggerType
from apps.utils.time import seconds_to_human


class EventsTable(tables.Table):
    type = tables.Column(accessor="type", verbose_name="When...")
    action_type = tables.Column(accessor="action__action_type", verbose_name="Then...")
    action_params = tables.JSONColumn(accessor="action__action_params", verbose_name="With these parameters...")
    total_num_triggers = tables.Column(accessor="total_num_triggers", verbose_name="Repeat")

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
        template_name = "django_tables2/bootstrap.html"
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        fields = (
            "type",
            "action_type",
            "action_params",
        )
