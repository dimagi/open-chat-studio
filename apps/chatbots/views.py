import uuid

from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.chat.channels import WebChannel
from apps.chatbots.forms import ChatbotForm, CopyChatbotForm
from apps.chatbots.tables import ChatbotSessionsTable, ChatbotTable
from apps.experiments.decorators import experiment_session_view, verify_session_access_cookie
from apps.experiments.models import Experiment, SessionStatus
from apps.experiments.tables import ExperimentVersionsTable
from apps.experiments.tasks import async_create_experiment_version
from apps.experiments.views import CreateExperiment, ExperimentSessionsTableView, ExperimentVersionsTableView
from apps.experiments.views.experiment import (
    BaseExperimentView,
    CreateExperimentVersion,
    base_single_experiment_view,
    experiment_chat,
    experiment_chat_embed,
    experiment_chat_session,
    experiment_invitations,
    experiment_version_details,
    start_session_public,
    start_session_public_embed,
    version_create_status,
)
from apps.generics.views import generic_home, paginate_session, render_session_details
from apps.pipelines.views import _pipeline_node_default_values, _pipeline_node_parameter_values, _pipeline_node_schemas
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.teams.decorators import login_and_team_required, team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.models import Flag
from apps.utils.base_experiment_table_view import BaseExperimentTableView


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def chatbots_home(request, team_slug: str):
    return generic_home(request, team_slug, "Chatbots", "chatbots:table", "chatbots:new")


class ChatbotExperimentTableView(BaseExperimentTableView):
    model = Experiment
    table_class = ChatbotTable
    permission_required = "experiments.view_experiment"

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(pipeline__isnull=False)


class CreateChatbot(CreateExperiment, BaseExperimentView):
    template_name = "chatbots/chatbot_form.html"
    form_class = ChatbotForm
    title = "Create Chatbot"
    button_title = "Create"
    permission_required = "experiments.add_experiment"

    @property
    def extra_context(self):
        context = super().extra_context
        context["active_tab"] = "chatbots"
        return context

    def get_success_url(self):
        return reverse("chatbots:edit", args=[self.request.team.slug, self.object.id])


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def single_chatbot_home(request, team_slug: str, experiment_id: int):
    return base_single_experiment_view(
        request, team_slug, experiment_id, "chatbots/single_chatbot_home.html", "chatbots"
    )


class EditChatbot(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "pipelines.change_pipeline"
    template_name = "pipelines/pipeline_builder.html"

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        llm_providers = LlmProvider.objects.filter(team=self.request.team).values("id", "name", "type").all()
        llm_provider_models = LlmProviderModel.objects.for_team(self.request.team).all()
        experiment = get_object_or_404(Experiment.objects.get_all(), id=kwargs["pk"], team=self.request.team)

        return {
            **data,
            "pipeline_id": experiment.pipeline_id,
            "node_schemas": _pipeline_node_schemas(),
            "experiment": experiment,
            "parameter_values": _pipeline_node_parameter_values(self.request.team, llm_providers, llm_provider_models),
            "default_values": _pipeline_node_default_values(llm_providers, llm_provider_models),
            "origin": "chatbots",
            "flags_enabled": [flag.name for flag in Flag.objects.all() if flag.is_active_for_team(self.request.team)],
        }


class CreateChatbotVersion(CreateExperimentVersion):
    permission_required = "experiments.add_experiment"
    pk_url_kwarg = "experiment_id"
    template_name = "experiments/create_version_form.html"

    def get_success_url(self):
        url = reverse(
            "chatbots:single_chatbot_home",
            kwargs={
                "team_slug": self.request.team.slug,
                "experiment_id": self.kwargs["experiment_id"],
            },
        )
        return f"{url}#versions"


class ChatbotVersionsTableView(ExperimentVersionsTableView):
    model = Experiment
    paginate_by = 25
    table_class = ExperimentVersionsTable
    template_name = "experiments/experiment_version_table.html"
    permission_required = "experiments.view_experiment"
    entity_type = "chatbots"

    def get_table(self, **kwargs):
        table_data = self.get_table_data()
        return self.table_class(data=table_data, entity_type="chatbots", **kwargs)


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def chatbot_version_details(request, team_slug: str, experiment_id: int, version_number: int):
    return experiment_version_details(request, team_slug, experiment_id, version_number)


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def chatbot_version_create_status(
    request,
    team_slug: str,
    experiment_id: int,
):
    return version_create_status(request, team_slug, experiment_id)


class ChatbotSessionsTableView(ExperimentSessionsTableView):
    table_class = ChatbotSessionsTable


@experiment_session_view()
@verify_session_access_cookie
def chatbot_session_details_view(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    return render_session_details(
        request,
        team_slug,
        experiment_id,
        session_id,
        active_tab="chatbots",
        template_path="chatbots/chatbot_session_view.html",
        session_type="Chatbot",
    )


@login_and_team_required
def chatbot_chat_session(request, team_slug: str, experiment_id: int, session_id: int, version_number: int):
    return experiment_chat_session(request, team_slug, experiment_id, session_id, version_number, "chatbots")


@login_and_team_required
def chatbot_session_pagination_view(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    return paginate_session(
        request,
        team_slug,
        experiment_id,
        session_id,
        view_name="chatbots:chatbot_session_view",
    )


@require_POST
@login_and_team_required
def start_authed_web_session(request, team_slug: str, experiment_id: int, version_number: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    session = WebChannel.start_new_session(
        working_experiment=experiment,
        participant_user=request.user,
        participant_identifier=request.user.email,
        timezone=request.session.get("detected_tz", None),
        version=version_number,
    )
    return HttpResponseRedirect(
        reverse("chatbots:chatbot_chat_session", args=[team_slug, experiment_id, version_number, session.id])
    )


@login_and_team_required
def chatbot_invitations(request, team_slug: str, experiment_id: int):
    return experiment_invitations(request, team_slug, experiment_id, "chatbots")


@team_required
def start_chatbot_session_public(request, team_slug: str, experiment_id: uuid.UUID):
    return start_session_public(request, team_slug, experiment_id)


@experiment_session_view(allowed_states=[SessionStatus.ACTIVE, SessionStatus.SETUP])
@verify_session_access_cookie
def chatbot_chat(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    return experiment_chat(request, team_slug, experiment_id, session_id)


@xframe_options_exempt
@team_required
def start_chatbot_session_public_embed(request, team_slug: str, experiment_id: uuid.UUID):
    return start_session_public_embed(request, team_slug, experiment_id)


@experiment_session_view(allowed_states=[SessionStatus.ACTIVE, SessionStatus.SETUP])
@xframe_options_exempt
def chatbot_chat_embed(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    return experiment_chat_embed(request, team_slug, experiment_id, session_id)


@login_and_team_required
def copy_chatbot(request, team_slug, *args, **kwargs):
    if request.method == "POST":
        form = CopyChatbotForm(request.POST)
        if form.is_valid():
            new_name = form.cleaned_data["new_name"]
            experiment = get_object_or_404(Experiment.objects.get_all(), id=kwargs["pk"], team=request.team)
            # copy chatbot
            new_experiment = experiment.create_new_version(make_default=False, is_copy=True, name=new_name)
            # create default version for copied chatbot
            task_id = async_create_experiment_version.delay(
                experiment_id=new_experiment.id, version_description="", make_default=True
            )
            new_experiment.create_version_task_id = task_id
            new_experiment.save(update_fields=["create_version_task_id"])
        referer = request.headers.get("referer")
        if "experiments" in referer:
            return redirect("experiments:single_experiment_home", team_slug=team_slug, experiment_id=new_experiment.id)
        return redirect("chatbots:single_chatbot_home", team_slug=team_slug, experiment_id=new_experiment.id)
    else:
        experiment_id = kwargs["pk"]
        return single_chatbot_home(request, team_slug, experiment_id)
