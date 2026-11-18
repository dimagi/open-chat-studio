from django.urls import reverse
from django.utils.translation import gettext


def user_teams(request):
    if not (hasattr(request, "user") and request.user.is_authenticated):
        return {}

    if request.htmx:
        # htmx requests don't need this context
        return {}

    current_team = getattr(request, "team", None)
    if not current_team:
        return {}
    other_membership = request.user.membership_set.exclude(team=current_team).select_related("team")
    other_teams = []
    for membership in other_membership:
        team = membership.team
        if team.is_pending_deletion:
            url = reverse("teams:pending_deletion", args=[team.slug])
            other_teams.append((gettext("{team} (pending deletion)").format(team=team.name), url))
        else:
            other_teams.append((team.name, team.dashboard_url))
    return {"other_teams": sorted(other_teams, key=lambda x: x[0].lower())}
