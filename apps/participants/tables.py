from django.conf import settings
from django_tables2 import columns, tables

from apps.experiments.models import Participant
from apps.generics import actions


class ParticipantTable(tables.Table):
    channel = columns.Column(accessor="get_platform_display", order_by="platform")
    created_at = columns.DateTimeColumn(verbose_name="Created On", format="Y-m-d H:i:s")
    actions = actions.ActionsColumn(
        actions=[
            actions.delete_action(
                url_name="participants:participant_delete",
                required_permissions=["experiments.delete_participant"],
                confirm_message="Are you sure you want to delete this participant? All associated data and sessions will be permanently removed.",
            ),
        ],
        orderable=False,
    )

    class Meta:
        model = Participant
        fields = ("name", "channel", "identifier", "created_at", "remote_id")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        empty_text = "No participants found."
        order_by = ("-created_at", "name", "identifier")
