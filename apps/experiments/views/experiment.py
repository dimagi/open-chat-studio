import logging
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import pytz
from celery.result import AsyncResult
from celery_progress.backend import Progress
from django.contrib import messages
from django.contrib.auth.decorators import permission_required, user_passes_test
from django.contrib.postgres.search import SearchVector
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, UpdateView
from django_tables2 import SingleTableView

from apps.channels.forms import ChannelForm
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.decorators import experiment_session_view
from apps.experiments.email import send_experiment_invitation
from apps.experiments.export import experiment_to_csv
from apps.experiments.forms import ConsentForm, ExperimentInvitationForm, SurveyForm
from apps.experiments.helpers import get_real_user_or_none
from apps.experiments.models import Experiment, ExperimentSession, Participant, SessionStatus, SyntheticVoice
from apps.experiments.tables import ExperimentTable
from apps.experiments.tasks import get_response_for_webchat_task
from apps.service_providers.utils import get_llm_provider_choices
from apps.teams.decorators import login_and_team_required
from apps.users.models import CustomUser


@login_and_team_required
def experiments_home(request, team_slug: str):
    return TemplateResponse(
        request,
        "generic/object_home.html",
        {
            "active_tab": "experiments",
            "title": "Experiments",
            "new_object_url": reverse("experiments:new", args=[team_slug]),
            "table_url": reverse("experiments:table", args=[team_slug]),
            "enable_search": True,
        },
    )


class ExperimentTableView(SingleTableView):
    model = Experiment
    paginate_by = 25
    table_class = ExperimentTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        query_set = Experiment.objects.filter(team=self.request.team)
        search = self.request.GET.get("search")
        if search:
            query_set = query_set.annotate(document=SearchVector("name", "description")).filter(document=search)
        return query_set


class ExperimentViewMixin:
    def get_form(self):
        form = super().get_form()
        _apply_related_model_querysets(self.request.team, form)
        _apply_voice_provider_alpine_attrs(form)
        return form

    def form_valid(self, form):
        experiment = form.instance
        if experiment.conversational_consent_enabled and not experiment.seed_message:
            messages.error(
                request=self.request, message=("A seed message is required when conversational " "consent is enabled!")
            )
            return render(self.request, self.template_name, self.get_context_data())

        if _source_material_is_missing(experiment):
            messages.error(request=self.request, message="The prompt expects source material, but none were specified")
            return render(self.request, self.template_name, self.get_context_data())
        return super().form_valid(form)


class CreateExperiment(ExperimentViewMixin, CreateView):
    model = Experiment
    fields = [
        "name",
        "description",
        "llm_provider",
        "llm",
        "temperature",
        "chatbot_prompt",
        "safety_layers",
        "tools_enabled",
        "conversational_consent_enabled",
        "source_material",
        "seed_message",
        "pre_survey",
        "post_survey",
        "consent_form",
        "voice_provider",
        "synthetic_voice",
        "no_activity_config",
    ]
    template_name = "experiments/experiment_form.html"

    @property
    def extra_context(self):
        return {
            **{
                "title": "Create Experiment",
                "button_text": "Create",
                "active_tab": "experiments",
            },
            **_get_voice_provider_alpine_context(self.request),
        }

    def get_success_url(self):
        return reverse("experiments:single_experiment_home", args=[self.request.team.slug, self.object.pk])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.owner = self.request.user
        return super().form_valid(form)


class EditExperiment(ExperimentViewMixin, UpdateView):
    model = Experiment
    fields = [
        "name",
        "description",
        "llm_provider",
        "llm",
        "temperature",
        "chatbot_prompt",
        "safety_layers",
        "tools_enabled",
        "conversational_consent_enabled",
        "source_material",
        "seed_message",
        "pre_survey",
        "post_survey",
        "consent_form",
        "voice_provider",
        "synthetic_voice",
        "no_activity_config",
    ]
    template_name = "experiments/experiment_form.html"

    @property
    def extra_context(self):
        return {
            **{
                "title": "Update Experiment",
                "button_text": "Update",
                "active_tab": "experiments",
            },
            **_get_voice_provider_alpine_context(self.request),
        }

    def get_queryset(self):
        return Experiment.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("experiments:single_experiment_home", args=[self.request.team.slug, self.object.pk])


def _source_material_is_missing(experiment: Experiment) -> bool:
    prompt = experiment.chatbot_prompt.prompt
    prompt_expects_source_material = "{source_material}" in prompt
    if not prompt_expects_source_material:
        return False
    return not bool(experiment.source_material)


def _apply_related_model_querysets(team, form):
    form.fields["llm_provider"].queryset = team.llmprovider_set
    form.fields["voice_provider"].queryset = team.voiceprovider_set
    form.fields["chatbot_prompt"].queryset = team.prompt_set
    form.fields["safety_layers"].queryset = team.safetylayer_set
    form.fields["source_material"].queryset = team.sourcematerial_set
    form.fields["pre_survey"].queryset = team.survey_set
    form.fields["post_survey"].queryset = team.survey_set
    form.fields["consent_form"].queryset = team.consentform_set
    form.fields["no_activity_config"].queryset = team.noactivitymessageconfig_set


def _apply_voice_provider_alpine_attrs(form):
    form.fields["voice_provider"].widget.attrs = {
        "x-model.fill": "voiceProvider",
    }
    form.fields["llm_provider"].widget.attrs = {
        "x-model.number.fill": "llmProvider",
    }
    # special template for dynamic select options
    form.fields["synthetic_voice"].widget.template_name = "django/forms/widgets/select_dynamic.html"
    form.fields["llm"].widget.template_name = "django/forms/widgets/select_dynamic.html"


def _get_voice_provider_alpine_context(request):
    """Add context required by the experiments/experiment_form.html template."""
    return {
        "form_attrs": {"x-data": "experiment"},
        # map provider ID to provider type
        "voice_providers_types": dict(request.team.voiceprovider_set.values_list("id", "type")),
        "synthetic_voice_options": sorted(
            [
                {"value": voice.id, "text": str(voice), "type": voice.service.lower()}
                for voice in SyntheticVoice.objects.all()
            ],
            key=lambda v: v["text"],
        ),
        "llm_options": get_llm_provider_choices(request.team),
    }


@login_and_team_required
def delete_experiment(request, team_slug: str, pk: int):
    safety_layer = get_object_or_404(Experiment, id=pk, team=request.team)
    safety_layer.delete()
    return redirect("experiments:experiments_home", team_slug)


@login_and_team_required
def single_experiment_home(request, team_slug: str, experiment_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    user_sessions = ExperimentSession.objects.filter(
        user=request.user,
        experiment=experiment,
    )
    channels = experiment.experimentchannel_set.exclude(platform="web").all()
    used_platforms = {channel.platform_enum for channel in channels}
    available_platforms = set(ChannelPlatform.for_dropdown()) - used_platforms
    platform_forms = {}
    form_kwargs = {"team": request.team}
    for platform in available_platforms:
        if platform.form(**form_kwargs):
            platform_forms[platform] = platform.form(**form_kwargs)

    return TemplateResponse(
        request,
        "experiments/single_experiment_home.html",
        {
            "active_tab": "experiments",
            "experiment": experiment,
            "user_sessions": user_sessions,
            "platforms": available_platforms,
            "platform_forms": platform_forms,
            "channels": channels,
        },
    )


@login_and_team_required
def create_channel(request, team_slug: str, experiment_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    existing_platforms = {channel.platform_enum for channel in experiment.experimentchannel_set.all()}
    form = ChannelForm(data=request.POST)
    if not form.is_valid():
        messages.error(request, "Form has errors: " + form.errors.as_text())
    else:
        platform = ChannelPlatform(form.cleaned_data["platform"])

        if platform in existing_platforms:
            messages.error(request, f"Channel for {platform.label} already exists")
            return redirect("experiments:single_experiment_home", team_slug, experiment_id)

        extra_form = platform.extra_form(data=request.POST)
        config_data = {}
        if extra_form:
            if extra_form.is_valid():
                config_data = extra_form.cleaned_data
            else:
                messages.error(request, "Channel data has errors: " + extra_form.errors.as_text())
                return redirect("experiments:single_experiment_home", team_slug, experiment_id)
        form.save(experiment, config_data)
    return redirect("experiments:single_experiment_home", team_slug, experiment_id)


@login_and_team_required
def update_delete_channel(request, team_slug: str, experiment_id: int, channel_id: int):
    channel = get_object_or_404(
        ExperimentChannel, id=channel_id, experiment_id=experiment_id, experiment__team__slug=team_slug
    )
    if request.POST.get("action") == "delete":
        channel.delete()
        return redirect("experiments:single_experiment_home", team_slug, experiment_id)

    form = channel.form(data=request.POST)
    if not form.is_valid():
        messages.error(request, "Form has errors: " + form.errors.as_text())
    else:
        extra_form = channel.extra_form(data=request.POST)
        config_data = {}
        if extra_form:
            if extra_form.is_valid():
                config_data = extra_form.cleaned_data
            else:
                messages.error(request, "Channel data has errors: " + extra_form.errors.as_text())
                return redirect("experiments:single_experiment_home", team_slug, experiment_id)
        form.save(channel.experiment, config_data)
    return redirect("experiments:single_experiment_home", team_slug, experiment_id)


def _start_experiment_session(
    experiment: Experiment,
    experiment_channel: ExperimentChannel,
    user: Optional[CustomUser] = None,
    participant: Optional[Participant] = None,
    external_chat_id: Optional[str] = None,
) -> ExperimentSession:
    session = ExperimentSession.objects.create(
        team=experiment.team,
        user=user,
        participant=participant,
        experiment=experiment,
        llm=experiment.llm,
        external_chat_id=external_chat_id,
        experiment_channel=experiment_channel,
    )
    return _check_and_process_seed_message(session)


def _check_and_process_seed_message(session: ExperimentSession):
    if session.experiment.seed_message:
        session.seed_task_id = get_response_for_webchat_task.delay(
            session.id, message_text=session.experiment.seed_message
        ).task_id
        session.save()
    return session


@require_POST
@login_and_team_required
def start_session(request, team_slug: str, experiment_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    experiment_channel = _ensure_experiment_channel_exists(
        experiment=experiment, platform="web", name=f"{experiment.id}-web"
    )
    session = _start_experiment_session(experiment, experiment_channel=experiment_channel, user=request.user)
    return HttpResponseRedirect(
        reverse("experiments:experiment_chat_session", args=[team_slug, experiment_id, session.id])
    )


def _ensure_experiment_channel_exists(experiment: Experiment, platform: str, name: str) -> ExperimentChannel:
    channel, _created = ExperimentChannel.objects.get_or_create(experiment=experiment, platform=platform, name=name)
    return channel


@login_and_team_required
def experiment_chat_session(request, team_slug: str, experiment_id: int, session_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    session = get_object_or_404(ExperimentSession, user=request.user, experiment_id=experiment_id, id=session_id)
    return TemplateResponse(
        request,
        "experiments/experiment_chat.html",
        {
            "experiment": experiment,
            "session": session,
            "active_tab": "experiments",
        },
    )


@require_POST
def experiment_session_message(request, team_slug: str, experiment_id: int, session_id: int):
    message_text = request.POST["message"]
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    # hack for anonymous user/teams
    user = get_real_user_or_none(request.user)
    session = get_object_or_404(ExperimentSession, user=user, experiment_id=experiment_id, id=session_id)
    result = get_response_for_webchat_task.delay(session.id, message_text)
    return TemplateResponse(
        request,
        "experiments/chat/experiment_response_htmx.html",
        {
            "experiment": experiment,
            "session": session,
            "message_text": message_text,
            "task_id": result.task_id,
        },
    )


# @login_and_team_required
def get_message_response(request, team_slug: str, experiment_id: int, session_id: int, task_id: str):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    # hack for anonymous user/teams
    user = get_real_user_or_none(request.user)
    session = get_object_or_404(ExperimentSession, user=user, experiment_id=experiment_id, id=session_id)
    last_message = ChatMessage.objects.filter(chat=session.chat).order_by("-created_at").first()
    progress = Progress(AsyncResult(task_id))
    return TemplateResponse(
        request,
        "experiments/chat/chat_message_response.html",
        {
            "experiment": experiment,
            "session": session,
            "task_id": task_id,
            "progress": progress.get_info(),
            "last_message_datetime": last_message and quote(last_message.created_at.isoformat()),
        },
    )


def poll_messages(request, team_slug: str, experiment_id: int, session_id: int):
    user = get_real_user_or_none(request.user)
    params = request.GET.dict()
    since_param = params.get("since")
    experiment_session = get_object_or_404(
        ExperimentSession, user=user, experiment_id=experiment_id, id=session_id, team=request.team
    )

    since = datetime.now().astimezone(pytz.timezone("UTC"))
    if since_param and since_param != "null":
        try:
            since = datetime.fromisoformat(since_param)
        except ValueError as e:
            logging.exception(f"Unexpected `since` parameter value. Error: {e}")

    messages = (
        ChatMessage.objects.filter(message_type=ChatMessageType.AI, chat=experiment_session.chat, created_at__gt=since)
        .order_by("created_at")
        .all()
    )
    last_message = messages[0] if messages else None

    return TemplateResponse(
        request,
        "experiments/chat/system_message.html",
        {
            "messages": [message.content for message in messages],
            "last_message_datetime": last_message and quote(last_message.created_at.isoformat()),
        },
    )


def start_experiment(request, team_slug: str, experiment_id: str):
    try:
        experiment = get_object_or_404(Experiment, public_id=experiment_id, is_active=True, team=request.team)
    except ValidationError:
        # old links dont have uuids
        raise Http404

    consent = experiment.consent_form
    if request.method == "POST":
        form = ConsentForm(consent, request.POST)
        if form.is_valid():
            # start anonymous experiment
            participant = None
            if form.cleaned_data.get("identifier"):
                participant = Participant.objects.get_or_create(
                    team=request.team, identifier=form.cleaned_data["identifier"]
                )[0]
            experiment_channel = _ensure_experiment_channel_exists(
                experiment=experiment, platform="web", name=f"{experiment.id}-web"
            )
            session = _start_experiment_session(
                experiment,
                user=get_real_user_or_none(request.user),
                participant=participant,
                experiment_channel=experiment_channel,
            )
            return _record_consent_and_redirect(request, team_slug, session)

    else:
        form = ConsentForm(
            consent,
            initial={
                "experiment_id": experiment.id,
            },
        )

    consent_notice = consent.get_rendered_content()
    return TemplateResponse(
        request,
        "experiments/start_experiment_session.html",
        {
            "active_tab": "experiments",
            "experiment": experiment,
            "consent_notice": mark_safe(consent_notice),
            "form": form,
        },
    )


@login_and_team_required
@permission_required("experiments.invite_participants", raise_exception=True)
def experiment_invitations(request, team_slug: str, experiment_id: str):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    sessions = experiment.sessions.order_by("-created_at").filter(
        status__in=["setup", "pending"],
        participant__isnull=False,
    )
    form = ExperimentInvitationForm(initial={"experiment_id": experiment.id})
    if request.method == "POST":
        post_form = ExperimentInvitationForm(request.POST)
        if post_form.is_valid():
            participant = Participant.objects.get_or_create(
                team=request.team, identifier=post_form.cleaned_data["email"]
            )[0]
            if ExperimentSession.objects.filter(
                team=request.team,
                experiment=experiment,
                participant=participant,
                status__in=["setup", "pending"],
            ).exists():
                messages.info(request, "{} already has a pending invitation.".format(participant))
            else:
                session = ExperimentSession.objects.create(
                    team=request.team,
                    experiment=experiment,
                    llm=experiment.llm,
                    status="setup",
                    participant=participant,
                )
                if post_form.cleaned_data["invite_now"]:
                    send_experiment_invitation(session)
        else:
            form = post_form

    return TemplateResponse(
        request,
        "experiments/experiment_invitations.html",
        {
            "invitation_form": form,
            "experiment": experiment,
            "sessions": sessions,
        },
    )


@require_POST
@permission_required("experiments.download_chats", raise_exception=True)
def download_experiment_chats(request, team_slug: str, experiment_id: str):
    # todo: this could be made more efficient and should be async, but just shipping something for now
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)

    # Create a HttpResponse with the CSV data and file attachment headers
    response = HttpResponse(experiment_to_csv(experiment).getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{experiment.name}-export.csv"'
    return response


def send_invitation(request, team_slug: str, experiment_id: str, session_id: str):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    session = ExperimentSession.objects.get(experiment=experiment, public_id=session_id)
    send_experiment_invitation(session)
    return TemplateResponse(
        request,
        "experiments/manage/invite_row.html",
        context={"request": request, "experiment": experiment, "session": session},
    )


def _record_consent_and_redirect(request, team_slug: str, experiment_session: ExperimentSession):
    # record consent, update status
    experiment_session.consent_date = timezone.now()
    experiment_session.user = get_real_user_or_none(request.user)
    if experiment_session.experiment.pre_survey:
        experiment_session.status = SessionStatus.PENDING_PRE_SURVEY
        redirct_url_name = "experiments:experiment_pre_survey"
    else:
        experiment_session.status = SessionStatus.ACTIVE
        redirct_url_name = "experiments:experiment_chat"
    experiment_session.save()
    return HttpResponseRedirect(
        reverse(
            redirct_url_name,
            args=[team_slug, experiment_session.experiment.public_id, experiment_session.public_id],
        )
    )


@experiment_session_view(allowed_states=[SessionStatus.SETUP, SessionStatus.PENDING])
def start_experiment_session(request, team_slug: str, experiment_id: str, session_id: str):
    experiment = get_object_or_404(Experiment, public_id=experiment_id, team=request.team)
    experiment_session = get_object_or_404(ExperimentSession, experiment=experiment, public_id=session_id)
    consent = experiment.consent_form

    initial = {
        "experiment_id": experiment.id,
    }
    if experiment_session.participant:
        initial["participant_id"] = experiment_session.participant.id
        initial["identifier"] = experiment_session.participant.identifier
    elif not request.user.is_anonymous:
        initial["identifier"] = request.user.email

    if request.method == "POST":
        form = ConsentForm(consent, request.POST, initial=initial)
        if form.is_valid():
            _check_and_process_seed_message(experiment_session)
            return _record_consent_and_redirect(request, team_slug, experiment_session)

    else:
        form = ConsentForm(consent, initial=initial)

    consent_notice = consent.get_rendered_content()
    return TemplateResponse(
        request,
        "experiments/start_experiment_session.html",
        {
            "active_tab": "experiments",
            "experiment": experiment,
            "consent_notice": mark_safe(consent_notice),
            "form": form,
        },
    )


@experiment_session_view(allowed_states=[SessionStatus.PENDING_PRE_SURVEY])
def experiment_pre_survey(request, team_slug: str, experiment_id: str, session_id: str):
    if request.method == "POST":
        form = SurveyForm(request.POST)
        if form.is_valid():
            request.experiment_session.status = SessionStatus.ACTIVE
            request.experiment_session.save()
            return HttpResponseRedirect(
                reverse(
                    "experiments:experiment_chat",
                    args=[team_slug, experiment_id, session_id],
                )
            )
    else:
        form = SurveyForm()
    return TemplateResponse(
        request,
        "experiments/pre_survey.html",
        {
            "active_tab": "experiments",
            "form": form,
            "experiment": request.experiment,
            "experiment_session": request.experiment_session,
        },
    )


@experiment_session_view(allowed_states=[SessionStatus.ACTIVE, SessionStatus.SETUP])
def experiment_chat(request, team_slug: str, experiment_id: str, session_id: str):
    return TemplateResponse(
        request,
        "experiments/experiment_chat.html",
        {
            "experiment": request.experiment,
            "session": request.experiment_session,
            "active_tab": "experiments",
        },
    )


@experiment_session_view(allowed_states=[SessionStatus.ACTIVE, SessionStatus.SETUP])
@require_POST
def end_experiment(request, team_slug: str, experiment_id: str, session_id: str):
    experiment_session = request.experiment_session
    experiment_session.ended_at = timezone.now()
    experiment_session.status = SessionStatus.PENDING_REVIEW
    experiment_session.save()
    return HttpResponseRedirect(reverse("experiments:experiment_review", args=[team_slug, experiment_id, session_id]))


@experiment_session_view(allowed_states=[SessionStatus.PENDING_REVIEW])
def experiment_review(request, team_slug: str, experiment_id: str, session_id: str):
    form = None
    if request.method == "POST":
        # no validation needed
        request.experiment_session.status = SessionStatus.COMPLETE
        request.experiment_session.reviewed_at = timezone.now()
        request.experiment_session.save()
        return HttpResponseRedirect(
            reverse("experiments:experiment_complete", args=[team_slug, experiment_id, session_id])
        )
    elif request.experiment.post_survey:
        form = SurveyForm()

    return TemplateResponse(
        request,
        "experiments/experiment_review.html",
        {
            "experiment": request.experiment,
            "experiment_session": request.experiment_session,
            "active_tab": "experiments",
            "form": form,
        },
    )


@experiment_session_view(allowed_states=[SessionStatus.COMPLETE])
def experiment_complete(request, team_slug: str, experiment_id: str, session_id: str):
    return TemplateResponse(
        request,
        "experiments/experiment_complete.html",
        {
            "experiment": request.experiment,
            "experiment_session": request.experiment_session,
            "active_tab": "experiments",
        },
    )


@experiment_session_view()
def experiment_session_view(request, team_slug: str, experiment_id: str, session_id: str):
    return TemplateResponse(
        request,
        "experiments/experiment_session_view.html",
        {
            "experiment": request.experiment,
            "experiment_session": request.experiment_session,
            "active_tab": "experiments",
        },
    )
