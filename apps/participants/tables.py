from django.conf import settings
from django_tables2 import columns, tables

from apps.experiments.models import Participant
from apps.generics import actions


class ParticpantTable(tables.Table):
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
        fields = ("identifier",)
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No participants found."
