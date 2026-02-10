import json
import logging
from pathlib import Path

import pydantic
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.help.agent import build_system_agent
from apps.pipelines.nodes.nodes import DEFAULT_FUNCTION
from apps.teams.decorators import login_and_team_required

logger = logging.getLogger("ocs.help")


_system_prompt = None


def _get_system_prompt():
    global _system_prompt
    if _system_prompt is None:
        _system_prompt = (Path(__file__).parent / "code_generate_system_prompt.md").read_text()
    return _system_prompt


@require_POST
@login_and_team_required
@csrf_exempt
def pipeline_generate_code(request, team_slug: str):
    body = json.loads(request.body)
    user_query = body["query"]
    current_code = body["context"]
    try:
        completion = code_completion(user_query, current_code)
    except Exception:
        logger.exception("An error occurred while generating code.")
        return JsonResponse({"error": "An error occurred while generating code."})
    return JsonResponse({"response": completion})


def code_completion(user_query, current_code, error=None, iteration_count=0) -> str:
    if iteration_count > 3:
        return current_code

    if current_code == DEFAULT_FUNCTION:
        current_code = ""

    system_prompt = _get_system_prompt()
    prompt_context = {"current_code": "", "error": ""}

    if current_code:
        prompt_context["current_code"] = f"The current function definition is:\n\n{current_code}"
    if error:
        prompt_context["error"] = f"\nThe current function has the following error. Try to resolve it:\n\n{error}"

    system_prompt = system_prompt.format(**prompt_context).strip()

    system_prompt += (
        "\n\nIMPORTANT: Start your response with exactly"
        " `def main(input: str, **kwargs) -> str:` and nothing else before it."
    )
    agent = build_system_agent("high", system_prompt)
    response = agent.invoke(
        {
            "messages": [
                {"role": "user", "content": user_query},
            ]
        }
    )

    response_code = response["messages"][-1].text

    from apps.pipelines.nodes.nodes import CodeNode

    try:
        CodeNode.model_validate({"code": response_code, "name": "code", "node_id": "code", "django_node": None})
    except pydantic.ValidationError as e:
        error = str(e)
        return code_completion(user_query, response_code, error, iteration_count=iteration_count + 1)

    return response_code
