import django_tables2 as tables
from django.conf import settings
from django.urls import reverse

from apps.generics import actions
from apps.generics.actions import chip_action
from apps.generics.tables import TimeAgoColumn
from apps.trace.models import Trace


def _chip_chatbot_url_factory(_, __, record, ___):
    return reverse("chatbots:single_chatbot_home", args=[record.team.slug, record.experiment_id])


def _chip_session_url_factory(_, __, record, ___):
    return reverse(
        "experiments:experiment_session_view",
        args=[record.team.slug, record.experiment.public_id, record.session.external_id],
    )


class TraceTable(tables.Table):
    timestamp = TimeAgoColumn(verbose_name="Last activity", orderable=True)
    chatbot = actions.ActionsColumn(
        actions=[
            chip_action(
                label_factory=lambda record, _: record.experiment.name,
                url_factory=_chip_chatbot_url_factory,
            ),
        ],
        align="left",
        orderable=True,
    )
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

    def render_duration(self, value):
        duration_seconds = round(value / 1000, 2)
        return f"{duration_seconds}s"

    class Meta:
        model = Trace
        fields = ("timestamp", "chatbot", "session", "duration")
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
        }
        orderable = False
        empty_text = "No chatbots found."
