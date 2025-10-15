import django_tables2 as tables
from django.conf import settings

from apps.custom_actions.models import CustomAction
from apps.generics import actions


class CustomActionTable(tables.Table):
    name = tables.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(
                "custom_actions:edit",
                required_permissions=["custom_actions.change_customaction"],
            ),
            actions.delete_action(
                "custom_actions:delete",
                required_permissions=["custom_actions.delete_customaction"],
            ),
        ]
    )

    class Meta:
        model = CustomAction
        fields = ("name", "description", "server_url")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No actions found."
