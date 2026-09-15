from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.teams.decorators import login_and_team_required
from apps.teams.forms import TeamMetadataForm


@login_and_team_required
def internal_metadata(request, team_slug):
    """Staff-only page for viewing and editing a team's internal metadata."""
    if not request.user.is_staff:
        raise Http404

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
                (_("Team Settings"), reverse("single_team:manage_team", args=[team.slug])),
                (_("Internal Metadata"), None),
            ],
        },
    )
