def team(request):
    return {
        "team": getattr(request, "team", None),
    }
