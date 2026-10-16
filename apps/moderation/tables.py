import json

from django.conf import settings
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django_tables2 import columns, tables

from apps.moderation.models import DeniedParticipant, DeniedParticipantSource
from apps.teams.utils import section_url


def blocked_by_label(denial: DeniedParticipant) -> str:
    """Who or what added the participant to the denylist."""
    if denial.source == DeniedParticipantSource.AUTO:
        return _("Auto-block")
    if denial.source == DeniedParticipantSource.CODE_NODE:
        return _("Pipeline")
    return denial.created_by.get_display_name() if denial.created_by else _("Deleted user")


class DeniedParticipantTable(tables.Table):
    """The team's denylist. Cells are rendered in Python: a template cell would rerun the context processors per row."""

    participant = columns.Column(verbose_name="Participant")
    channel = columns.Column(accessor="participant__get_platform_display", verbose_name="Channel")
    blocked_by = columns.Column(empty_values=(), verbose_name="Blocked by")
    reason = columns.Column(verbose_name="Reason", empty_values=())
    created_at = columns.DateTimeColumn(verbose_name="Date", format="Y-m-d H:i")
    actions = columns.Column(empty_values=(), verbose_name="")

    def render_participant(self, record):
        url = reverse("participants:single-participant-home", args=[self.request.team.slug, record.participant_id])
        return format_html('<a class="link link-hover font-semibold" href="{}">{}</a>', url, record.participant)

    def render_blocked_by(self, record):
        return blocked_by_label(record)

    def render_reason(self, value):
        return value or "—"

    def render_actions(self, record):
        team_slug = self.request.team.slug
        button_id = f"unblock-denied-participant-{record.id}"
        confirm = _("Unblock this participant? They will be able to use your chatbots again.")
        return format_html(
            '<button id="{id}" type="button" class="btn btn-xs btn-outline" hx-post="{url}" hx-vals="{vals}"'
            ' hx-trigger="confirmed" onclick="confirmThenTrigger(\'{id}\', {confirm})">{label}</button>',
            id=button_id,
            url=reverse("participants:unblock_participant", args=[team_slug, record.participant_id]),
            vals=json.dumps({"next": section_url(team_slug=team_slug, section_key="moderation")}),
            confirm=json.dumps(confirm),
            label=_("Unblock"),
        )

    class Meta:
        model = DeniedParticipant
        fields = ("participant", "channel", "blocked_by", "reason", "created_at", "actions")
        orderable = False
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        empty_text = "No participants are blocked."
