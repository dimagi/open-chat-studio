from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django_htmx.http import reswap, retarget, trigger_client_event
from django_tables2 import SingleTableView
from waffle import flag_is_active
from waffle.decorators import waffle_flag

from apps.moderation.forms import TeamModerationForm
from apps.moderation.models import DeniedParticipant
from apps.moderation.tables import DeniedParticipantTable
from apps.service_providers.utils import ServiceProvider, get_available_subtypes
from apps.service_providers.views import CreateServiceProvider
from apps.teams.decorators import login_and_team_required
from apps.teams.flags import Flags
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.views.team_settings import SettingsSection
from apps.web.forms import set_form_fields_disabled

SETTINGS_SECTION_KEY = "moderation"
PROVIDER_SAVED_EVENT = "moderation-provider-saved"
SETTINGS_FORM_TEMPLATE = "moderation/components/settings_form.html"


def _can_view_settings_section(request) -> bool:
    return bool(flag_is_active(request, Flags.ABUSE_DETECTION.slug)) and (
        request.team_membership.is_team_admin() or request.user.has_perm("experiments.change_participant")
    )


def moderation_settings_context(request, form: TeamModerationForm | None = None) -> dict:
    """Context for the Moderation section's settings form."""
    team = request.team
    can_edit = request.team_membership.is_team_admin()
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
        "can_view_denylist": request.user.has_perm("experiments.change_participant"),
        "denylist_table_url": reverse("moderation:denied_participants_table", args=[request.team.slug]),
    }


def _blocked_participant_count(request) -> int:
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


@login_and_team_required
@waffle_flag(Flags.ABUSE_DETECTION.slug)
@permission_required("teams.change_team", raise_exception=True)
@require_POST
def moderation_settings(request, team_slug: str):
    """Save the team's moderation switches and selected provider."""
    form = TeamModerationForm(request.POST, instance=request.team)
    if form.is_valid():
        form.save()
        messages.success(request, _("Moderation settings saved."))
        form = TeamModerationForm(instance=request.team)
    else:
        messages.error(request, _("Could not save the moderation settings."))
        # is_valid() has already copied the rejected values onto request.team.
        request.team.refresh_from_db()
    return render(request, SETTINGS_FORM_TEMPLATE, moderation_settings_context(request, form=form))


@method_decorator(waffle_flag(Flags.ABUSE_DETECTION.slug), name="dispatch")
class DeniedParticipantTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    """The team's blocked participants."""

    permission_required = "experiments.change_participant"
    table_class = DeniedParticipantTable
    template_name = "table/single_table.html"
    paginate_by = 25

    def get_queryset(self):
        return (
            DeniedParticipant.objects.filter(team=self.request.team)
            .select_related("participant__user", "created_by")
            .order_by("-created_at", "-id")
        )


@method_decorator(waffle_flag(Flags.ABUSE_DETECTION.slug), name="dispatch")
class ModerationProviderDialog(CreateServiceProvider):
    """The moderation provider create/edit form, shown in a dialog in the Moderation section.

    A successful save replaces the section's settings form, with the saved provider selected.
    """

    template_name = "moderation/components/provider_dialog.html"

    def saved_response(self, *, obj, had_connection_test_warning: bool):
        # The settings form's own fields are posted along with the dialog, so unsaved switch changes are kept.
        form = TeamModerationForm(
            instance=self.request.team,
            initial={
                "moderation_enabled": bool(self.request.POST.get("moderation_enabled")),
                "auto_block_enabled": bool(self.request.POST.get("auto_block_enabled")),
                "moderation_provider": obj.pk,
            },
        )
        context = moderation_settings_context(self.request, form=form)
        response = render(self.request, SETTINGS_FORM_TEMPLATE, context)
        response = reswap(retarget(response, "#moderation-settings"), "outerHTML")
        return trigger_client_event(response, PROVIDER_SAVED_EVENT)
