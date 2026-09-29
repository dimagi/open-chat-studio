from apps.teams.export.selection import selected_experiment_count


def team(request):
    team = getattr(request, "team", None)
    return {
        "team": team,
        "migrating_chatbot_count": selected_experiment_count(team) if team and team.is_migrating else 0,
    }
