from django.contrib import messages
from django.http import Http404, HttpResponseRedirect
from django.utils.translation import gettext_lazy as _

from apps.teams.decorators import login_and_team_required
from apps.teams.forms import TeamMetadataForm
from apps.teams.views.team_settings import render_team_settings, section_url

SECTION = "internal-metadata"


@login_and_team_required
def internal_metadata(request, team_slug):
    """Staff-only endpoint for saving a team's internal metadata."""
    if not request.user.is_staff:
        raise Http404

    if request.method != "POST":
        return HttpResponseRedirect(section_url(team_slug, SECTION))

    form = TeamMetadataForm(request.POST, team=request.team)
    if form.is_valid():
        form.save()
        messages.success(request, _("Internal metadata updated successfully."))
        return render_team_settings(request, SECTION)
    return render_team_settings(request, SECTION, metadata_form=form)
