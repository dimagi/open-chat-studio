import django_tables2 as tables
from django.conf import settings
from django.template.loader import get_template
from django.urls import reverse

from apps.generics import actions
from apps.generics.actions import chip_action
from apps.generics.tables import TimeAgoColumn
from apps.teams.utils import get_slug_for_team
from apps.trace.models import Trace


def _chip_chatbot_url_factory(_, request, record, __):
    return reverse("chatbots:single_chatbot_home", args=[get_slug_for_team(record.team_id), record.experiment_id])


def _chip_session_url_factory(_, request, record, __):
    return reverse(
        "chatbots:chatbot_session_view",
        args=[get_slug_for_team(record.team_id), record.experiment.public_id, record.session.external_id],
    )


def _get_chatbot_name(record):
    version = "unreleased"
    if record.experiment_version_number:
        version = f"v{record.experiment_version_number}"
    return f"{record.experiment.name} ({version})"


class TraceTable(tables.Table):
    timestamp = TimeAgoColumn(verbose_name="Timestamp", orderable=True)
    bot = actions.ActionsColumn(
        actions=[
            chip_action(
                label_factory=lambda record, _: _get_chatbot_name(record),
                url_factory=_chip_chatbot_url_factory,
            ),
        ],
        align="left",
        orderable=True,
    )
    duration = tables.Column(verbose_name="Duration", accessor="duration", orderable=True)
    session = actions.ActionsColumn(
        actions=[
            chip_action(
                label_factory=lambda record, _: record.participant.identifier,
                url_factory=_chip_session_url_factory,
            ),
        ],
        align="left",
        orderable=True,
    )

    def render_duration(self, record):
        return f"{record.duration_seconds()}s"

    def render_status(self, record):
        return get_template("trace/partials/status.html").render(context={"object": record})

    class Meta:
        model = Trace
        fields = ("timestamp", "bot", "session", "duration", "status")
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
        }
        orderable = False
        empty_text = "No chatbots found."
