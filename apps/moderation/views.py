from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django_htmx.http import reswap, retarget, trigger_client_event
from django_tables2 import SingleTableView
from waffle.decorators import waffle_flag

from apps.moderation.forms import TeamModerationForm
from apps.moderation.models import DeniedParticipant
from apps.moderation.settings_section import moderation_settings_context
from apps.moderation.tables import DeniedParticipantTable
from apps.service_providers.views import CreateServiceProvider
from apps.teams.decorators import login_and_team_required
from apps.teams.flags import Flags
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.settings_sections import is_team_admin

PROVIDER_SAVED_EVENT = "moderation-provider-saved"
SETTINGS_FORM_TEMPLATE = "moderation/components/settings_form.html"


@login_and_team_required
@waffle_flag(Flags.ABUSE_DETECTION.slug)
@require_POST
def moderation_settings(request, team_slug: str):
    """Save the team's moderation switches and selected provider."""
    if not is_team_admin(request):
        raise PermissionDenied
    form = TeamModerationForm(data=request.POST, instance=request.team)
    if form.is_valid():
        form.save()
        messages.success(request=request, message=_("Moderation settings saved."))
        form = TeamModerationForm(instance=request.team)
    else:
        messages.error(request=request, message=_("Could not save the moderation settings."))
        # is_valid() has already copied the rejected values onto request.team.
        request.team.refresh_from_db()
    return render(
        request=request,
        template_name=SETTINGS_FORM_TEMPLATE,
        context=moderation_settings_context(request=request, form=form),
    )


@method_decorator(decorator=waffle_flag(Flags.ABUSE_DETECTION.slug), name="dispatch")
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


@method_decorator(decorator=waffle_flag(Flags.ABUSE_DETECTION.slug), name="dispatch")
class ModerationProviderDialog(CreateServiceProvider):
    """The moderation provider create/edit form, shown in a dialog in the Moderation section.

    A successful save replaces the section's settings form, with the saved provider selected.
    """

    template_name = "moderation/components/provider_dialog.html"

    def has_permission(self):
        return is_team_admin(self.request) and super().has_permission()

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
        context = moderation_settings_context(request=self.request, form=form)
        response = render(request=self.request, template_name=SETTINGS_FORM_TEMPLATE, context=context)
        response = reswap(response=retarget(response=response, target="#moderation-settings"), method="outerHTML")
        return trigger_client_event(response=response, name=PROVIDER_SAVED_EVENT)
