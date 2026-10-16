"""The Moderation section of the team settings page."""

from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from waffle import flag_is_active

from apps.moderation.forms import TeamModerationForm
from apps.moderation.models import DeniedParticipant
from apps.service_providers.utils import ServiceProvider, get_available_subtypes
from apps.teams.flags import Flags
from apps.teams.settings_sections import SettingsSection, is_team_admin
from apps.web.forms import set_form_fields_disabled

SETTINGS_SECTION_KEY = "moderation"


def _can_view_denylist(request) -> bool:
    return request.user.has_perm("experiments.change_participant")


def _can_view_settings_section(request) -> bool:
    return bool(flag_is_active(request, Flags.ABUSE_DETECTION.slug)) and (
        is_team_admin(request) or _can_view_denylist(request)
    )


def moderation_settings_context(request, form: TeamModerationForm | None = None) -> dict:
    """Context for the Moderation section's settings form."""
    team = request.team
    can_edit = is_team_admin(request)
    if form is None:
        form = TeamModerationForm(instance=team)
    if not can_edit:
        set_form_fields_disabled(form, True)
    provider_type = ServiceProvider.moderation
    subtype = get_available_subtypes(provider_type, request)[0]
    return {
        "moderation_form": form,
        "moderation_providers": list(form.fields["moderation_provider"].queryset),
        "can_edit_moderation": can_edit,
        "can_add_moderation_provider": can_edit and request.user.has_perm(provider_type.get_permission("add")),
        "can_change_moderation_provider": can_edit and request.user.has_perm(provider_type.get_permission("change")),
        "moderation_provider_new_url": reverse("moderation:provider_new", args=[team.slug, str(subtype)]),
        # The selected provider's id replaces the 0 in the browser.
        "moderation_provider_edit_url": reverse("moderation:provider_edit", args=[team.slug, 0]),
        "moderation_subtype_label": subtype.label,
    }


def _settings_section_context(request) -> dict:
    return {
        **moderation_settings_context(request),
        "can_view_denylist": _can_view_denylist(request),
        "denylist_table_url": reverse("moderation:denied_participants_table", args=[request.team.slug]),
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
