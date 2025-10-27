def user_teams(request):
    if not (hasattr(request, "user") and request.user.is_authenticated):
        return {}

    if request.htmx:
        # htmx requests don't need this context
        return {}

    current_team = getattr(request, "team", None)
    if not current_team:
        return {}
    other_membership = request.user.membership_set
    if current_team:
        other_membership = other_membership.exclude(team=current_team)
    return {
        "other_teams": sorted(
            [
                (membership.team.name, membership.team.dashboard_url)
                for membership in other_membership.select_related("team")
            ],
            key=lambda x: x[0].lower(),
        ),
    }
