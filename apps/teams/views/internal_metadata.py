from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from apps.teams.breadcrumbs import team_settings_crumb
from apps.teams.decorators import login_and_team_required
from apps.teams.forms import TeamMetadataForm
from apps.web.elevation import Grant, requires_elevation


@login_and_team_required
@requires_elevation(Grant.OCS_ADMIN)
def internal_metadata(request, team_slug):
    """Staff-only page for viewing and editing a team's internal metadata."""
    team = request.team
    if request.method == "POST":
        form = TeamMetadataForm(request.POST, team=team)
        if form.is_valid():
            form.save()
            messages.success(request, _("Internal metadata updated successfully."))
            return redirect("single_team:manage_team", team_slug=team.slug)
    else:
        form = TeamMetadataForm(team=team)

    return render(
        request,
        "teams/internal_metadata.html",
        {
            "form": form,
            "team": team,
            "breadcrumbs": [
                team_settings_crumb(team.slug),
                (_("Internal Metadata"), None),
            ],
        },
    )
