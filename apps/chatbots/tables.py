import django_tables2 as tables
from django.conf import settings
from django.db.models import F
from django.urls import reverse
from django_tables2 import columns

from apps.experiments.models import Experiment
from apps.experiments.tables import ExperimentSessionsTable, _show_chat_button, session_chat_url
from apps.generics import actions
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
    participant_count = columns.Column(verbose_name="Participants", orderable=True)
    last_message = TimeAgoColumn(verbose_name="Last activity", orderable=True)
    session_count = ColumnWithHelp(
        verbose_name="Sessions", orderable=True, help_text="Active sessions in the last 30 days"
    )
    messages_count = ColumnWithHelp(
        verbose_name="Messages", orderable=True, help_text="Messages sent and received in the last 30 days"
    )
    error_trend = columns.TemplateColumn(
        verbose_name="Error Trend (last 48h)",
        template_name="table/barchart.html",
    )
    actions = columns.TemplateColumn(
        template_name="experiments/components/experiment_actions_column.html",
        extra_context={"type": "chatbots"},
    )

    class Meta:
        model = Experiment
        fields = ("name", "participant_count", "session_count", "messages_count", "last_message", "error_trend")
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
