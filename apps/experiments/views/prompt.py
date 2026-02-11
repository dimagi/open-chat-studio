import json
from collections import defaultdict
from datetime import timedelta

from celery.result import AsyncResult
from celery_progress.backend import Progress
from django.http import JsonResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.experiments.helpers import get_real_user_or_none
from apps.experiments.models import Experiment, PromptBuilderHistory, SourceMaterial
from apps.experiments.tasks import get_prompt_builder_response_task
from apps.service_providers.utils import get_llm_provider_choices
from apps.teams.decorators import login_and_team_required, team_required

PROMPT_DATA_SESSION_KEY = "prompt_data"


@login_and_team_required
def prompt_builder_load_experiments(request, team_slug: str):
    experiments = list(Experiment.objects.filter(team=request.team).values("id", "name", "prompt_text"))

    return TemplateResponse(
        request,
        "experiments/experiment_list.html",
        {
            "experiments": experiments,
        },
    )


@login_and_team_required
def prompt_builder_load_source_material(request, team_slug: str):
    source_material = SourceMaterial.objects.filter(team=request.team)
    source_material_list = list(source_material.values())

    return TemplateResponse(
        request,
        "experiments/source_material_list.html",
        {
            "source_materials": source_material_list,
        },
    )


@login_and_team_required
def experiments_prompt_builder(request, team_slug: str):
    llm_providers = list(request.team.llmprovider_set.all())

    return TemplateResponse(
        request,
        "experiments/prompt_builder.html",
        {
            "llm_options": get_llm_provider_choices(request.team),
            "llm_providers": llm_providers,
            "active_tab": "prompt_builder",
        },
    )


@require_POST
@login_and_team_required
def experiments_prompt_builder_get_message(request, team_slug: str):
    data = json.loads(request.body.decode("utf-8"))
    user = get_real_user_or_none(request.user)
    result = get_prompt_builder_response_task.delay(request.team.id, user.id, data)
    return JsonResponse({"task_id": result.task_id})


@team_required
def get_prompt_builder_message_response(request, team_slug: str):
    task_id = request.GET.get("task_id")
    progress = Progress(AsyncResult(task_id))
    return JsonResponse(
        {
            "task_id": task_id,
            "progress": progress.get_info(),
        },
    )


@login_and_team_required
def get_prompt_builder_history(request, team_slug: str):
    # Fetch history for the request user limited to last 30 days
    thirty_days_ago = timezone.now() - timedelta(days=30)
    histories = PromptBuilderHistory.objects.filter(
        team=request.team, owner=request.user, created_at__gte=thirty_days_ago
    ).order_by("-created_at")

    # Initialize temporary output format
    output_temp = defaultdict(list)

    for history_id, history in enumerate(histories):
        # Adding history_id to JSON history object
        history_data = history.history
        history_data["history_id"] = history_id

        # Customizing the output for each history
        event = {
            "history_id": history_id,
            "time": history.created_at.strftime("%H:%M"),
            "preview": history_data.get("preview", ""),
            "sourceMaterialName": history_data.get("sourceMaterialName", "None"),
            "sourceMaterialID": history_data.get("sourceMaterialID", -1),
            "temperature": history_data.get("temperature", 0.7),
            "prompt": history_data.get("prompt", ""),
            "inputFormatter": history_data.get("inputFormatter", ""),
            "provider": history_data.get("provider"),
            "providerModelId": history_data.get("providerModelId"),
            "messages": history_data.get("messages", []),
        }

        # Populating the temporary output dictionary
        output_temp[history.created_at.date()].append(event)

    # Convert to the final desired output format
    output_list = [
        {"date": date_obj.strftime("%A %d %b %Y"), "events": events} for date_obj, events in output_temp.items()
    ]

    return JsonResponse(output_list, safe=False)


@login_and_team_required
def prompt_builder_start_save_process(request, team_slug: str):
    prompt_data = json.loads(request.body)
    request.session[PROMPT_DATA_SESSION_KEY] = prompt_data
    return JsonResponse({"redirect_url": reverse("experiments:new", args=[team_slug])})
