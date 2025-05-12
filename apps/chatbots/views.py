import uuid

from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import TemplateView

from apps.chat.channels import WebChannel
from apps.chatbots.forms import ChatbotForm
from apps.chatbots.tables import ChatbotSessionsTable, ChatbotTable
from apps.experiments.decorators import experiment_session_view, verify_session_access_cookie
from apps.experiments.models import Experiment, SessionStatus, SyntheticVoice
from apps.experiments.tables import ExperimentVersionsTable
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
@permission_required("experiments.change_experiment", raise_exception=True)
@require_GET
def settings_edit_mode(request, team_slug, experiment_id):
    if request.team.slug != team_slug:
        return HttpResponse("Unauthorized", status=403)

    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    available_voice_providers = request.team.voiceprovider_set.all()
    available_synthetic_voices = SyntheticVoice.get_for_team(request.team)
    available_trace_providers = request.team.traceprovider_set.all()
    available_consent_forms = request.team.consentform_set.exclude(is_version=True)
    available_surveys = request.team.survey_set.exclude(is_version=True)

    context = {
        "experiment": experiment,
        "edit_mode": True,
        "available_voice_providers": available_voice_providers,
        "available_synthetic_voices": available_synthetic_voices,
        "available_trace_providers": available_trace_providers,
        "available_consent_forms": available_consent_forms,
        "available_surveys": available_surveys,
        "request": request,
    }

    return HttpResponse(render_to_string("chatbots/settings_content.html", context, request=request))


@login_and_team_required
@permission_required("experiments.change_experiment", raise_exception=True)
@require_GET
def cancel_edit_mode(request, team_slug, experiment_id):
    if request.team.slug != team_slug:
        return HttpResponse("Unauthorized", status=403)

    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    context = {
        "experiment": experiment,
        "request": request,
        "edit_mode": False,
    }
    return HttpResponse(render_to_string("chatbots/settings_content.html", context, request=request))


@login_and_team_required
@permission_required("experiments.change_experiment", raise_exception=True)
@require_POST
def save_all_settings(request, team_slug, experiment_id):
    if request.team.slug != team_slug:
        return HttpResponse("Unauthorized", status=403)
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    if "description" in request.POST:
        experiment.description = request.POST.get("description", "")

    if "seed_message" in request.POST:
        experiment.seed_message = request.POST.get("seed_message", "")
    if "voice_provider" in request.POST:
        provider_id = request.POST.get("voice_provider")
        if provider_id:
            experiment.voice_provider_id = provider_id
        else:
            experiment.voice_provider = None
    if "synthetic_voice" in request.POST:
        voice_id = request.POST.get("synthetic_voice")
        if voice_id:
            experiment.synthetic_voice_id = voice_id
        else:
            experiment.synthetic_voice = None
    if "trace_provider" in request.POST:
        provider_id = request.POST.get("trace_provider")
        if provider_id:
            experiment.trace_provider_id = provider_id
        else:
            experiment.trace_provider = None
    if "consent_form" in request.POST:
        form_id = request.POST.get("consent_form")
        if form_id:
            experiment.consent_form_id = form_id
        else:
            experiment.consent_form = None
    if "pre_survey" in request.POST:
        survey_id = request.POST.get("pre_survey")
        if survey_id:
            experiment.pre_survey_id = survey_id
        else:
            experiment.pre_survey = None

    if "post_survey" in request.POST:
        survey_id = request.POST.get("post_survey")
        if survey_id:
            experiment.post_survey_id = survey_id
        else:
            experiment.post_survey = None

    experiment.echo_transcript = "echo_transcript" in request.POST
    experiment.use_processor_bot_voice = "use_processor_bot_voice" in request.POST
    experiment.debug_mode_enabled = "debug_mode_enabled" in request.POST
    experiment.conversational_consent_enabled = "conversational_consent_enabled" in request.POST

    if "voice_response_behaviour" in request.POST:
        experiment.voice_response_behaviour = request.POST.get("voice_response_behaviour", "")

    if "participant_allowlist" in request.POST:
        raw_text = request.POST.get("participant_allowlist", "")
        identifiers = [line.strip() for line in raw_text.split("\n") if line.strip()]
        cleaned_identifiers = []
        for identifier in identifiers:
            cleaned_identifiers.append(identifier.replace(" ", ""))
        experiment.participant_allowlist = cleaned_identifiers

    experiment.save()

    context = {
        "experiment": experiment,
        "request": request,
        "edit_mode": False,
    }
    return HttpResponse(render_to_string("chatbots/settings_content.html", context, request=request))


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
