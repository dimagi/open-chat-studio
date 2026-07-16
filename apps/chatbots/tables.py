import dataclasses
import json

import django_tables2 as tables
from django.conf import settings
from django.db.models import F
from django.template.loader import get_template
from django.urls import reverse
from django_tables2 import columns
from waffle import flag_is_active

from apps.api.session_tokens import issue_session_token
from apps.experiments.models import Experiment, ExperimentSession
from apps.generics import actions, chips
from apps.generics.actions import chip_action
from apps.generics.tables import ArrayColumn, ColumnWithHelp, TimeAgoColumn
from apps.teams.utils import get_slug_for_team


def session_chat_url(url_name, request, record, value):
    return reverse(
        url_name, args=[request.team.slug, record.experiment_id, record.get_experiment_version_number(), record.id]
    )


def _show_chat_button(request, record):
    return record.participant.user == request.user and not record.is_complete and record.experiment.is_editable


@dataclasses.dataclass
class ContinueChatAction(actions.Action):
    """Continue Chat action. When the chat widget flag is active it opens the session in the embedded
    widget (a floating popup) instead of linking to the full-page chat UI."""

    template: str = "chatbots/components/continue_chat_action.html"

    def get_context(self, request, record, value):
        ctxt = super().get_context(request, record, value)
        if flag_is_active(request, "flag_chat_widget"):
            ctxt.update(
                {
                    "use_widget": True,
                    "chatbot_id": record.experiment.public_id,
                    "session_external_id": record.external_id,
                    "session_token": issue_session_token(record),
                    "version_number": record.get_experiment_version_number(),
                    "allow_attachments": record.experiment.file_uploads_enabled,
                }
            )
        return ctxt


def _name_label_factory(record, _):
    if record.is_archived:
        return f"{record.name} (archived)"
    return record.name


def _chatbot_url_factory(record):
    return reverse("chatbots:single_chatbot_home", args=[get_slug_for_team(record.team_id), record.id])


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
    last_activity = TimeAgoColumn(verbose_name="Last Activity", orderable=True)
    session_count = ColumnWithHelp(verbose_name="Total Sessions", orderable=True)
    interaction_count = ColumnWithHelp(verbose_name="Total Interactions", orderable=True)
    trends = columns.TemplateColumn(
        verbose_name="Trends (last 24h)",
        template_name="table/trends_chart.html",
    )
    actions = columns.TemplateColumn(
        template_name="experiments/components/experiment_actions_column.html",
        extra_context={"type": "chatbots"},
    )

    class Meta:
        model = Experiment
        fields = (
            "name",
            "participant_count",
            "session_count",
            "interaction_count",
            "last_activity",
            "trends",
        )
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
            "data-redirect-url": _chatbot_url_factory,
        }
        orderable = False
        empty_text = "No chatbots found."

    def render_trends(self, record):
        successes, errors = getattr(record, "trend_data", ([], []))
        template = get_template("table/trends_chart.html")
        return template.render(
            {
                "record": record,
                "trends_json": json.dumps({"successes": successes, "errors": errors}),
            }
        )

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
        args=[get_slug_for_team(record.team_id), record.experiment.public_id, record.external_id],
    )


class ChatbotSessionsTable(tables.Table):
    chatbot = columns.Column(
        verbose_name="Chatbot",
        accessor="experiment",
        orderable=True,
    )
    participant = columns.Column(accessor="participant", verbose_name="Participant", order_by="participant__identifier")
    message_count = columns.Column(
        verbose_name="Message Count",
        accessor="message_count",
        orderable=True,
    )
    last_message = TimeAgoColumn(accessor="last_activity_at", verbose_name="Last activity", orderable=True)
    tags = columns.TemplateColumn(verbose_name="Tags", template_name="annotations/tag_ui.html")
    versions = ArrayColumn(verbose_name="Versions", accessor="experiment_versions")
    state = columns.Column(verbose_name="State", accessor="status", orderable=True)
    remote_id = columns.Column(verbose_name="Remote Id", accessor="participant__remote_id")

    actions = actions.ActionsColumn(
        actions=[
            ContinueChatAction(
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

    def render_tags(self, record, bound_column):
        template = get_template(bound_column.column.template_name)
        return template.render({"object": record.chat})

    def render_participant(self, record):
        template = get_template("generic/chip.html")
        chip = record.get_participant_chip(include_link=self._user_has_perm("experiments.view_participant"))
        return template.render({"chip": chip})

    def render_chatbot(self, record):
        template = get_template("generic/chip.html")
        chatbot = record.experiment
        url = chatbot.get_absolute_url() if self._user_has_perm("experiments.view_experiment") else ""
        chip = chips.Chip(label=str(chatbot), url=url)
        return template.render({"chip": chip})

    def _user_has_perm(self, perm: str) -> bool:
        # `request` is only set when the table is built via RequestConfig/SingleTableView; guard
        # against a direct-instantiation caller, denying the link rather than raising AttributeError.
        request = getattr(self, "request", None)
        return bool(request and request.user.has_perm(perm))

    class Meta:
        model = ExperimentSession
        # Ensure that chatbot is shown first
        fields = ["chatbot", "participant", "message_count", "last_message"]
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No sessions yet!"
