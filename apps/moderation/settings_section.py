"""The Moderation section of the team settings page."""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from waffle import flag_is_active

from apps.chat.models import MODERATION_STATUS_LABELS, ModerationStatus
from apps.moderation.forms import TeamModerationForm
from apps.moderation.models import DeniedParticipant
from apps.moderation.services import ModerationCounts, count_moderated_messages
from apps.service_providers.utils import ServiceProvider, get_available_subtypes
from apps.teams.flags import Flags
from apps.teams.settings_sections import SettingsSection, is_team_admin
from apps.web.forms import set_form_fields_disabled

SETTINGS_SECTION_KEY = "moderation"
COUNTS_PERIOD = timedelta(days=7)


def _can_view_denylist(request) -> bool:
    return request.user.has_perm("experiments.change_participant")


def _can_view_settings_section(request) -> bool:
    return bool(flag_is_active(request=request, flag_name=Flags.ABUSE_DETECTION.slug)) and (
        is_team_admin(request) or _can_view_denylist(request)
    )


def moderation_settings_context(request, form: TeamModerationForm | None = None) -> dict:
    """Context for the Moderation section's settings form."""
    team = request.team
    can_edit = is_team_admin(request)
    if form is None:
        form = TeamModerationForm(instance=team)
    if not can_edit:
        set_form_fields_disabled(form=form, disabled=True)
    provider_type = ServiceProvider.moderation
    subtype = get_available_subtypes(provider=provider_type, request=request)[0]
    return {
        "moderation_form": form,
        "moderation_providers": list(form.fields["moderation_provider"].queryset),
        "can_edit_moderation": can_edit,
        "can_add_moderation_provider": can_edit and request.user.has_perm(provider_type.get_permission("add")),
        "can_change_moderation_provider": can_edit and request.user.has_perm(provider_type.get_permission("change")),
        "moderation_provider_new_url": reverse(viewname="moderation:provider_new", args=[team.slug, str(subtype)]),
        # The selected provider's id replaces the 0 in the browser.
        "moderation_provider_edit_url": reverse(viewname="moderation:provider_edit", args=[team.slug, 0]),
        "moderation_subtype_label": subtype.label,
    }


def _share(*, count: int, total: int) -> int | None:
    """`count` as a whole percentage of `total`, or None when there is nothing to divide by."""
    return round(100 * count / total) if total else None


def _message_counts_context(counts: ModerationCounts) -> dict:
    skip_reasons = sorted(counts.skip_reasons.items(), key=lambda item: (-item[1], item[0]))
    reason_counts = [(reason.label, count) for reason, count in skip_reasons]
    stats = []
    for status, title in MODERATION_STATUS_LABELS.items():
        count = counts.by_status.get(status, 0)
        stats.append(
            {
                "status": status.value,
                "title": title,
                "count": count,
                "share": _share(count=count, total=counts.total),
                "reasons": reason_counts if status == ModerationStatus.SKIPPED else [],
            }
        )
    return {"total": counts.total, "stats": stats, "mostly_rate_limited": counts.mostly_rate_limited}


def _settings_section_context(request) -> dict:
    counts = count_moderated_messages(team=request.team, since=timezone.now() - COUNTS_PERIOD)
    return {
        **moderation_settings_context(request=request),
        "message_counts": _message_counts_context(counts),
        "can_view_denylist": _can_view_denylist(request),
        "denylist_table_url": reverse(viewname="moderation:denied_participants_table", args=[request.team.slug]),
    }


def _blocked_participant_count(request) -> int | None:
    if not _can_view_denylist(request):
        return None
    return DeniedParticipant.objects.filter(team=request.team).count()


SETTINGS_SECTION = SettingsSection(
    key=SETTINGS_SECTION_KEY,
    label=_("Moderation"),
    icon="fa-shield-halved",
    template="moderation/settings_section.html",
    is_visible=_can_view_settings_section,
    get_context=_settings_section_context,
    get_count=_blocked_participant_count,
)
