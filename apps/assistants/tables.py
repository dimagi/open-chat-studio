from django.conf import settings
from django_tables2 import columns, tables

from apps.assistants.models import OpenAiAssistant
from apps.generics import actions


class OpenAiAssistantTable(tables.Table):
    name = columns.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        extra_context={
            "actions": [
                actions.edit_action(
                    "assistants:edit",
                    required_permissions=["assistants.change_openaiassistant"],
                ),
                actions.delete_action(
                    "assistants:delete",
                    required_permissions=["assistants.delete_openaiassistant"],
                ),
            ]
        },
    )

    class Meta:
        model = OpenAiAssistant
        fields = ("name", "assistant_id")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No assistants found."
