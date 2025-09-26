import django_tables2 as tables
from django.conf import settings
from django.db.models import F
from django.template.loader import get_template
from django.urls import reverse
from django_tables2 import columns

from apps.experiments.models import Experiment, ExperimentSession
from apps.experiments.tables import ExperimentSessionsTable, _show_chat_button, session_chat_url
from apps.generics import actions, chips
from apps.generics.actions import chip_action
from apps.generics.tables import ColumnWithHelp, TimeAgoColumn


def _name_label_factory(record, _):
    if record.is_archived:
        return f"{record.name} (archived)"
    return record.name


def _chatbot_url_factory(record):
    return reverse("chatbots:single_chatbot_home", args=[record.team.slug, record.id])


def _chip_chatbot_url_factory(_, __, record, ___):
    return _chatbot_url_factory(record)


class ChatbotTable(tables.Table):
    name = actions = actions.ActionsColumn(
        actions=[
            chip_action(
                label_factory=_name_label_factory,
                url_factory=_chip_chatbot_url_factory,
                # Note: Keep the styling consistent with `generic/chip_button.html`
                button_style="btn-soft btn-primary",
            ),
        ],
        align="left",
        orderable=True,
    )
    participant_count = columns.Column(verbose_name="Total Participants", orderable=True)
    last_message = TimeAgoColumn(verbose_name="Last activity", orderable=True)
    session_count = ColumnWithHelp(verbose_name="Total Sessions", orderable=True)
    messages_count = ColumnWithHelp(verbose_name="Total Messages", orderable=True)
    trends = columns.TemplateColumn(
        verbose_name="Trends (last 48h)",
        template_name="table/trends_chart.html",
    )
    actions = columns.TemplateColumn(
        template_name="experiments/components/experiment_actions_column.html",
        extra_context={"type": "chatbots"},
    )

    class Meta:
        model = Experiment
        fields = ("name", "participant_count", "session_count", "messages_count", "last_message", "trends")
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
            "data-redirect-url": _chatbot_url_factory,
        }
        orderable = False
        empty_text = "No chatbots found."

    def order_last_message(self, queryset, is_descending):
        order = F("last_message")
        if is_descending:
            order = order.desc(nulls_last=True)
        else:
            order = order.asc(nulls_last=True)
        queryset = queryset.order_by(order)
        return queryset, True


def chatbot_url_factory(_, __, record, value):
    return reverse(
        "chatbots:chatbot_session_view",
        args=[record.team.slug, record.experiment.public_id, record.external_id],
    )


class ChatbotSessionsTable(ExperimentSessionsTable):
    chatbot = columns.Column(
        verbose_name="Chatbot",
        accessor="experiment",
        orderable=True,
    )

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
    )

    def render_chatbot(self, record):
        template = get_template("generic/chip.html")
        chatbot = record.experiment
        chip = chips.Chip(label=str(chatbot), url=chatbot.get_absolute_url())
        return template.render({"chip": chip})

    class Meta:
        model = ExperimentSession
        # Ensure that chatbot is shown first
        fields = ["chatbot"]
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No sessions yet!"
