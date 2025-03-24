from collections.abc import Callable
from typing import Any

import django_tables2 as tables
from django.conf import settings
from django.urls import reverse
from django_tables2 import columns

from apps.experiments.models import Experiment
from apps.experiments.tables import ExperimentSessionsTable, _show_chat_button, session_chat_url
from apps.generics import actions
from apps.generics.actions import Action


class ChatbotTable(tables.Table):
    name = columns.Column(
        orderable=True,
    )
    description = columns.Column(verbose_name="Description")
    owner = columns.Column(accessor="owner__username", verbose_name="Created By")
    actions = columns.TemplateColumn(
        template_name="experiments/components/experiment_actions_column.html",
        extra_context={"type": "chatbots", "use_pipeline_id": True},
    )

    class Meta:
        model = Experiment
        fields = ("name",)
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
            "data-redirect-url": lambda record: reverse(
                "chatbots:single_chatbot_home", args=[record.team.slug, record.id]
            ),
        }
        orderable = False
        empty_text = "No experiments found."

    def render_name(self, record):
        if record.is_archived:
            return f"{record.name} (archived)"
        return record.name


def chatbot_chip_action(
    label: str = None,
    label_factory: Callable[[Any, Any], str] = None,
    required_permissions: list = None,
    display_condition: callable = None,
):
    if not label and not label_factory:

        def label_factory(record, value):
            return str(value)

    def url_factory(_, __, record, value):
        return reverse(
            "chatbots:chatbot_session_view",
            args=[record.team.slug, record.experiment.public_id, record.external_id],
        )

    return Action(
        url_name="",
        url_factory=url_factory,
        label=label,
        label_factory=label_factory,
        icon_class="fa-solid fa-external-link",
        button_style="",
        required_permissions=required_permissions,
        display_condition=display_condition,
    )


class ChatbotSessionsTable(ExperimentSessionsTable):
    actions = actions.ActionsColumn(
        actions=[
            actions.Action(
                url_name="chatbots:chatbot_chat_session",
                url_factory=session_chat_url,
                icon_class="fa-solid fa-comment",
                title="Continue Chat",
                display_condition=_show_chat_button,
            ),
            chatbot_chip_action(
                label="Session Details",
            ),
        ],
        align="right",
    )
