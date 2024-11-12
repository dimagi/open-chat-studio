from django.conf import settings
from django.utils.safestring import mark_safe
from django_tables2 import columns, tables

from apps.assistants.models import OpenAiAssistant
from apps.generics import actions


def get_assistant_row_attrs():
    def _get_redirect_url(record):
        return record.get_absolute_url() if record.working_version is None else ""

    return {
        **settings.DJANGO_TABLES2_ROW_ATTRS,
        "data-redirect-url": _get_redirect_url,
    }


class OpenAiAssistantTable(tables.Table):
    name = columns.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(
                "assistants:edit",
                required_permissions=["assistants.change_openaiassistant"],
                display_condition=lambda request, record: record.working_version is None,
            ),
            actions.AjaxAction(
                "assistants:sync",
                title="Update from OpenAI",
                icon_class="fa-solid fa-rotate",
                required_permissions=["assistants.change_openaiassistant"],
                display_condition=lambda request, record: record.working_version is None,
            ),
            actions.delete_action(
                "assistants:delete_local",
                required_permissions=["assistants.delete_openaiassistant"],
                confirm_message="This will only delete the assistant from the local system.",
            ),
            actions.AjaxAction(
                "assistants:delete",
                title="Delete from OpenAI",
                icon_class="fa-solid fa-trash-arrow-up",
                required_permissions=["assistants.delete_openaiassistant"],
                confirm_message="This will also delete the assistant from OpenAI. Are you sure?",
                hx_method="delete",
            ),
        ]
    )

    @property
    def name_linkify(self):
        return lambda record: record.working_version is not None

    def render_name(self, record):
        if self.name_linkify(record):
            return mark_safe(f'<a href="{record.get_absolute_url()}">{record.name}</a>')
        else:
            return record.name

    class Meta:
        model = OpenAiAssistant
        fields = ("name", "assistant_id")
        row_attrs = get_assistant_row_attrs()
        orderable = False
        empty_text = "No assistants found."
