from django.conf import settings
from django_tables2 import columns, tables

from apps.cost_tracking.templatetags.cost_tracking import cost_display
from apps.experiments.models import Participant
from apps.generics import actions


class ParticipantTable(tables.Table):
    channel = columns.Column(accessor="get_platform_display", order_by="platform")
    created_at = columns.DateTimeColumn(verbose_name="Created On", format="Y-m-d H:i:s")
    # Sorting is deliberately off: ordering every participant by cost would scan
    # the team's whole UsageRecord history (no `(team, participant)` index), so
    # cost is computed per page in ParticipantTableView.get_table.
    cost = columns.Column(verbose_name="Cost (30d)", empty_values=(), orderable=False)
    actions = actions.ActionsColumn(
        actions=[
            actions.delete_action(
                url_name="participants:participant_delete",
                required_permissions=["experiments.delete_participant"],
                confirm_message="Are you sure you want to delete this participant? All associated data and sessions"
                " will be permanently removed.",
            ),
        ],
        orderable=False,
    )

    def render_cost(self, record):
        cost_map = getattr(self, "cost_map", {})
        return f"${cost_display(cost_map.get(record.id))}"

    class Meta:
        model = Participant
        fields = ("name", "channel", "identifier", "created_at", "remote_id")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        empty_text = "No participants found."
        order_by = ("-created_at", "name", "identifier")
