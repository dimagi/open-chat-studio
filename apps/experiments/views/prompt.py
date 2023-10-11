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
from apps.experiments.models import Prompt, PromptBuilderHistory, SourceMaterial
from apps.experiments.tasks import get_prompt_builder_response_task
from apps.teams.decorators import login_and_team_required, team_admin_required


@login_and_team_required
def prompt_builder_load_prompts(request, team_slug: str):
    prompts = Prompt.objects.all()
    prompts_list = list(prompts.values())

    return TemplateResponse(
        request,
        "experiments/prompts_list.html",
        {
            "prompts": prompts_list,
        },
    )


@login_and_team_required
def prompt_builder_load_source_material(request, team_slug: str):
    source_material = SourceMaterial.objects.all()
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
    prompts = Prompt.objects.order_by("-created_at").all()
    prompts_list = list(prompts.values())

    return TemplateResponse(
        request,
        "experiments/prompt_builder.html",
        {
            "prompts": prompts_list,
            "active_tab": "prompt_builder",
        },
    )


@require_POST
@login_and_team_required
def experiments_prompt_builder_get_message(request, team_slug: str):
    data_json = request.body.decode("utf-8")
    user = get_real_user_or_none(request.user)
    result = get_prompt_builder_response_task.delay(user.id, data_json)
    return JsonResponse({"task_id": result.task_id})


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
    histories = PromptBuilderHistory.objects.filter(owner=request.user, created_at__gte=thirty_days_ago).order_by(
        "-created_at"
    )

    # Initialize temporary output format
    output_temp = defaultdict(list)

    history_id = 0  # Initial history_id for the oldest item
    for history in histories:
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
            "model": history_data.get("model", "gpt-4"),
            "messages": history_data.get("messages", []),
        }

        # Populating the temporary output dictionary
        output_temp[history.created_at.date()].append(event)

        history_id += 1  # Incrementing history_id for each newer history

    # Convert to the final desired output format
    output_list = [
        {"date": date_obj.strftime("%A %d %b %Y"), "events": events} for date_obj, events in output_temp.items()
    ]

    return JsonResponse(output_list, safe=False)


@login_and_team_required
@team_admin_required
def prompt_builder_start_save_process(request, team_slug: str):
    # Get your long data
    long_data = json.loads(request.body)

    # Save it in session
    request.session["long_data"] = long_data

    # Redirect to admin add page
    return JsonResponse({"redirect_url": reverse("admin:experiments_prompt_add")})
