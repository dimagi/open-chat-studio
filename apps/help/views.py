import json
import logging

from django.http import JsonResponse
from django.views.decorators.http import require_POST
from pydantic import ValidationError

import apps.help.agents  # noqa: F401 — trigger agent registration
from apps.help.registry import AGENT_REGISTRY
from apps.teams.decorators import login_and_team_required

logger = logging.getLogger("ocs.help")


@require_POST
@login_and_team_required
def run_agent(request, team_slug: str, agent_name: str):
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        return JsonResponse({"error": f"Unknown agent: {agent_name}"}, status=404)

    try:
        body = json.loads(request.body)
        agent = agent_cls(input=body)
    except (json.JSONDecodeError, ValidationError) as e:
        logger.error("Agent '%s' input error: %s", agent_name, e)
        return JsonResponse({"error": "Invalid input."}, status=400)

    try:
        result = agent.run()
        return JsonResponse({"response": result.model_dump()})
    except Exception:
        logger.exception("Agent '%s' failed.", agent_name)
        return JsonResponse({"error": "An error occurred."}, status=500)
