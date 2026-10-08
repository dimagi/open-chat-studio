from apps.teams.export.selection import migrating_chatbot_count


def team(request):
    team = getattr(request, "team", None)
    return {
        "team": team,
        "migrating_chatbot_count": migrating_chatbot_count(team) if team else 0,
    }
