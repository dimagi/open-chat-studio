import json
from collections import defaultdict
from datetime import timedelta

from celery.result import AsyncResult
from celery_progress.backend import Progress
from django.contrib import messages
from django.contrib.postgres.search import SearchVector
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, UpdateView
from django_tables2 import SingleTableView
from langchain.prompts import PromptTemplate

from apps.experiments.helpers import get_real_user_or_none
from apps.experiments.models import Prompt, PromptBuilderHistory, SourceMaterial
from apps.experiments.tables import PromptTable
from apps.experiments.tasks import get_prompt_builder_response_task
from apps.teams.decorators import login_and_team_required

PROMPT_DATA_SESSION_KEY = "prompt_data"


@login_and_team_required
def prompt_home(request, team_slug: str):
    return TemplateResponse(
        request,
        "generic/object_home.html",
        {
            "active_tab": "prompts",
            "title": "Prompts",
            "new_object_url": reverse("experiments:prompt_new", args=[team_slug]),
            "table_url": reverse("experiments:prompt_table", args=[team_slug]),
            "enable_search": True,
        },
    )


class PromptTableView(SingleTableView):
    model = Prompt
    paginate_by = 25
    table_class = PromptTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        query_set = Prompt.objects.filter(team=self.request.team)
        search = self.request.GET.get("search")
        if search:
            query_set = query_set.annotate(document=SearchVector("name", "description", "prompt")).filter(
                document=search
            )
        return query_set


class PromptViewMixin:
    ALLOWED_PROMPT_VARIABLES = ["source_material"]

    def form_valid(self, form):
        input_variables = set(PromptTemplate.from_template(form.data["prompt"]).input_variables)
        disallowed_variables = input_variables - set(self.ALLOWED_PROMPT_VARIABLES)
        if disallowed_variables:
            error_message = f"""Unexpected variables found in the prompt: {disallowed_variables}. Use the input formatter to
                format the user input"""
            messages.error(request=self.request, message=error_message)
            return render(self.request, self.template_name, self.get_context_data())
        return super().form_valid(form)


class CreatePrompt(PromptViewMixin, CreateView):
    model = Prompt
    fields = [
        "name",
        "description",
        "prompt",
        "input_formatter",
    ]
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Prompt",
        "button_text": "Create",
        "active_tab": "prompts",
    }

    def get_success_url(self):
        return reverse("experiments:prompt_home", args=[self.request.team.slug])

    def get_initial(self):
        initial = super().get_initial()
        long_data = self.request.session.pop(PROMPT_DATA_SESSION_KEY, None)
        if long_data:
            initial.update(long_data)
        return initial

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.owner = self.request.user
        return super().form_valid(form)


class EditPrompt(PromptViewMixin, UpdateView):
    model = Prompt
    fields = [
        "name",
        "description",
        "prompt",
        "input_formatter",
    ]
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update Prompt",
        "button_text": "Update",
        "active_tab": "prompts",
    }

    def get_queryset(self):
        return Prompt.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("experiments:prompt_home", args=[self.request.team.slug])


@login_and_team_required
def delete_prompt(request, team_slug: str, pk: int):
    prompt = get_object_or_404(Prompt, id=pk, team=request.team)
    prompt.delete()
    return HttpResponse()


@login_and_team_required
def prompt_builder_load_prompts(request, team_slug: str):
    prompts = Prompt.objects.filter(team=request.team)
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
    prompts = Prompt.objects.filter(team=request.team).order_by("-created_at").all()
    prompts_list = list(prompts.values())

    llm_providers = list(request.team.llmprovider_set.all())
    return TemplateResponse(
        request,
        "experiments/prompt_builder.html",
        {
            "prompts": prompts_list,
            "llm_providers": llm_providers,
            "default_llm_provider": llm_providers[0] if llm_providers else None,
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
def prompt_builder_start_save_process(request, team_slug: str):
    prompt_data = json.loads(request.body)
    request.session[PROMPT_DATA_SESSION_KEY] = prompt_data
    return JsonResponse({"redirect_url": reverse("experiments:prompt_new", args=[team_slug])})
