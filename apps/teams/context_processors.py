from django.urls import reverse


def team(request):
    return {
        "team": getattr(request, "team", None),
        "notices": get_team_notices(request),
    }


def get_team_notices(request):
    team = getattr(request, "team", None)
    if not team:
        return []

    return filter(None, [notice(request, team) for notice in NOTICES])


def _create_llm_provider(request, team):
    if request.resolver_match.view_name == "service_providers:new":
        return

    if team.llmprovider_set.exists():
        return

    return """You need to create an <a href="{url}">LLM Provider</a> before you can continue.""".format(
        url=reverse("service_providers:new", kwargs={"team_slug": team.slug, "provider_type": "llm"})
    )


NOTICES = [
    _create_llm_provider,
]
