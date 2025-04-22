import django_tables2 as tables
from django.conf import settings
from django.urls import reverse
from django_tables2 import columns

from apps.experiments.models import Experiment
from apps.experiments.tables import ExperimentSessionsTable, _show_chat_button, session_chat_url
from apps.generics import actions
from apps.generics.actions import chip_action


class ChatbotTable(tables.Table):
    name = columns.Column(
        orderable=True,
    )
    description = columns.Column(verbose_name="Description")
    owner = columns.Column(accessor="owner__username", verbose_name="Created By")
    actions = columns.TemplateColumn(
        template_name="experiments/components/experiment_actions_column.html",
        extra_context={"type": "chatbots"},
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


def chatbot_url_factory(_, __, record, value):
    return reverse(
        "chatbots:chatbot_session_view",
        args=[record.team.slug, record.experiment.public_id, record.external_id],
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
            chip_action(
                label="Session Details",
                url_factory=chatbot_url_factory,
            ),
        ],
        align="right",
        orderable=False,
    )
