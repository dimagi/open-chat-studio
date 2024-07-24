def user_teams(request):
    if not request.user.is_authenticated:
        return {}

    current_team = getattr(request, "team", None)
    if not current_team:
        return {}
    other_membership = request.user.membership_set
    if current_team:
        other_membership = other_membership.exclude(team=current_team)
    return {
        "other_teams": {
            membership.team.name: membership.team.dashboard_url
            for membership in other_membership.select_related("team")
        }
    }
