from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.teams.decorators import login_and_team_required
from apps.teams.forms import FeatureFlagForm
from apps.teams.views.team_settings import render_team_settings

SECTION = "flags"


@login_and_team_required
@require_POST
def feature_flags(request, team_slug):
    """Save the team's feature flags and re-render the settings section."""
    team = request.team
    if not request.team_membership.is_team_admin():
        messages.error(request, _("Sorry you don't have permission to do that."))
        return render_team_settings(request, SECTION)

    form = FeatureFlagForm(request.POST, team=team)
    if form.is_valid():
        form.save()
        messages.success(request, _("Feature flags updated successfully."))
        # Rebuild unbound so the checkboxes reflect what was saved, including flags
        # auto-enabled as requirements of another.
        return render_team_settings(request, SECTION)
    return render_team_settings(request, SECTION, flags_form=form)
