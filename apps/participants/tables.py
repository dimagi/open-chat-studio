from django.conf import settings
from django_tables2 import columns, tables

from apps.experiments.models import Participant
from apps.generics import actions


class ParticipantTable(tables.Table):
    created_at = columns.DateTimeColumn(verbose_name="Created On", format="Y-m-d H:i:s")
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        extra_context={
            "actions": [
                actions.edit_action(url_name="participants:participant_edit"),
            ]
        },
    )

    class Meta:
        model = Participant
        fields = ("identifier", "created_at")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        empty_text = "No participants found."
        order_by = ("-created_at", "identifier")
