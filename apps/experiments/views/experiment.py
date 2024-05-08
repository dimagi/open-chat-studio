import logging
import uuid
from datetime import datetime
from urllib.parse import quote

import pytz
from celery.result import AsyncResult
from celery_progress.backend import Progress
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.postgres.search import SearchVector
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, Count, IntegerField, When
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, UpdateView
from django_tables2 import SingleTableView
from langchain_core.prompts import PromptTemplate
from waffle import flag_is_active

from apps.annotations.models import Tag
from apps.channels.forms import ChannelForm
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.events.models import (
    EventLogStatusChoices,
    StaticTrigger,
    StaticTriggerType,
    TimeoutTrigger,
)
from apps.events.tables import (
    EventsTable,
)
from apps.events.tasks import enqueue_static_triggers
from apps.experiments.decorators import experiment_session_view, set_session_access_cookie, verify_session_access_cookie
from apps.experiments.email import send_experiment_invitation
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.experiments.export import experiment_to_csv
from apps.experiments.forms import (
    ConsentForm,
    ExperimentInvitationForm,
    SurveyCompletedForm,
)
from apps.experiments.helpers import get_real_user_or_none
from apps.experiments.models import Experiment, ExperimentSession, Participant, SessionStatus, SyntheticVoice
from apps.experiments.tables import ExperimentSessionsTable, ExperimentTable
from apps.experiments.tasks import get_response_for_webchat_task
from apps.experiments.views.prompt import PROMPT_DATA_SESSION_KEY
from apps.files.forms import get_file_formset
from apps.files.views import BaseAddFileHtmxView, BaseDeleteFileView
from apps.service_providers.utils import get_llm_provider_choices
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.users.models import CustomUser


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def experiments_home(request, team_slug: str):
    return TemplateResponse(
        request,
        "generic/object_home.html",
        {
            "active_tab": "experiments",
            "title": "Experiments",
            "info_link": settings.DOCUMENTATION_LINKS["experiment"],
            "new_object_url": reverse("experiments:new", args=[team_slug]),
            "table_url": reverse("experiments:table", args=[team_slug]),
            "enable_search": True,
        },
    )


class ExperimentTableView(SingleTableView, PermissionRequiredMixin):
    model = Experiment
    paginate_by = 25
    table_class = ExperimentTable
    template_name = "table/single_table.html"
    permission_required = "experiments.view_experiment"

    def get_queryset(self):
        query_set = Experiment.objects.filter(team=self.request.team)
        search = self.request.GET.get("search")
        if search:
            query_set = query_set.annotate(document=SearchVector("name", "description")).filter(document=search)
        return query_set


class ExperimentSessionsTableView(SingleTableView, PermissionRequiredMixin):
    model = ExperimentSession
    paginate_by = 25
    table_class = ExperimentSessionsTable
    template_name = "table/single_table.html"
    permission_required = "annotations.view_customtaggeditem"

    def get_queryset(self):
        query_set = ExperimentSession.objects.with_last_message_created_at().filter(
            team=self.request.team, experiment__id=self.kwargs["experiment_id"]
        )
        tags_query = self.request.GET.get("tags")
        if tags_query:
            tags = tags_query.split("&")
            query_set = query_set.filter(chat__tags__name__in=tags).distinct()
        return query_set


class ExperimentForm(forms.ModelForm):
    PROMPT_HELP_TEXT = """
        Use {source_material} to place source material in the prompt. Use {participant_data} to place participant
        data in the prompt.
    """
    type = forms.ChoiceField(
        choices=[("llm", gettext("Base Language Model")), ("assistant", gettext("OpenAI Assistant"))],
        widget=forms.RadioSelect(attrs={"x-model": "type"}),
    )
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    prompt_text = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}), required=False, help_text=PROMPT_HELP_TEXT)
    input_formatter = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    seed_message = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)

    class Meta:
        model = Experiment
        fields = [
            "name",
            "description",
            "llm_provider",
            "llm",
            "assistant",
            "max_token_limit",
            "temperature",
            "prompt_text",
            "input_formatter",
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
            "safety_violation_notification_emails",
            "voice_response_behaviour",
        ]
        labels = {
            "source_material": "Inline Source Material",
        }
        help_texts = {
            "source_material": "Use the '{source_material}' tag to inject source material directly into your prompt.",
            "assistant": "If you have an OpenAI assistant, you can select it here to use it for this experiment.",
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        team = request.team
        exclude_services = [SyntheticVoice.OpenAIVoiceEngine]
        if flag_is_active(request, "open_ai_voice_engine"):
            exclude_services = []

        # Limit to team's data
        self.fields["llm_provider"].queryset = team.llmprovider_set
        self.fields["assistant"].queryset = team.openaiassistant_set
        self.fields["voice_provider"].queryset = team.voiceprovider_set.exclude(
            syntheticvoice__service__in=exclude_services
        )
        self.fields["safety_layers"].queryset = team.safetylayer_set
        self.fields["source_material"].queryset = team.sourcematerial_set
        self.fields["pre_survey"].queryset = team.survey_set
        self.fields["post_survey"].queryset = team.survey_set
        self.fields["consent_form"].queryset = team.consentform_set
        self.fields["no_activity_config"].queryset = team.noactivitymessageconfig_set
        self.fields["synthetic_voice"].queryset = SyntheticVoice.get_for_team(team, exclude_services)

        # Alpine.js bindings
        self.fields["voice_provider"].widget.attrs = {
            "x-model.fill": "voiceProvider",
        }
        self.fields["llm_provider"].widget.attrs = {
            "x-model.number.fill": "llmProviderId",
        }
        # special template for dynamic select options
        self.fields["synthetic_voice"].widget.template_name = "django/forms/widgets/select_dynamic.html"
        self.fields["llm"].widget.template_name = "django/forms/widgets/select_dynamic.html"

    def clean(self):
        cleaned_data = super().clean()
        errors = {}
        bot_type = cleaned_data["type"]
        if bot_type == "llm":
            cleaned_data["assistant"] = None
            if not cleaned_data.get("prompt_text"):
                errors["prompt_text"] = "Prompt text is required unless you select an OpenAI Assistant"
            if not cleaned_data.get("llm_provider"):
                errors["llm_provider"] = "LLM Provider is required unless you select an OpenAI Assistant"
            if not cleaned_data.get("llm"):
                errors["llm"] = "LLM is required unless you select an OpenAI Assistant"
        else:
            if not cleaned_data.get("assistant"):
                errors["assistant"] = "Assistant is required when creating an assistant experiment"

        if errors:
            raise forms.ValidationError(errors)

        _validate_prompt_variables(cleaned_data)
        return cleaned_data

    def save(self, commit=True):
        experiment = super().save(commit=False)
        experiment.team = self.request.team
        experiment.owner = self.request.user
        if commit:
            experiment.save()
            self.save_m2m()
        return experiment


def _validate_prompt_variables(form_data):
    required_variables = set(PromptTemplate.from_template(form_data.get("prompt_text")).input_variables)
    available_variables = set(["participant_data"])
    if form_data.get("source_material"):
        available_variables.add("source_material")
    missing_vars = required_variables - available_variables
    known_vars = {"source_material", "participant_data"}
    if missing_vars:
        errors = []
        unknown_vars = missing_vars - known_vars
        if unknown_vars:
            errors.append("Prompt contains unknown variables: " + ", ".join(unknown_vars))
            missing_vars -= unknown_vars
        if missing_vars:
            errors.append(f"Prompt expects {', '.join(missing_vars)} but it is not provided.")
        raise forms.ValidationError({"prompt_text": errors})


class BaseExperimentView(LoginAndTeamRequiredMixin, PermissionRequiredMixin):
    model = Experiment
    template_name = "experiments/experiment_form.html"
    form_class = ExperimentForm

    @property
    def extra_context(self):
        experiment_type = "assistant" if self.object and self.object.assistant_id else "llm"
        if self.request.POST.get("type"):
            experiment_type = self.request.POST.get("type")
        return {
            **{
                "title": self.title,
                "button_text": self.button_title,
                "active_tab": "experiments",
                "experiment_type": experiment_type,
            },
            **_get_voice_provider_alpine_context(self.request),
        }

    def get_success_url(self):
        return reverse("experiments:single_experiment_home", args=[self.request.team.slug, self.object.pk])

    def get_queryset(self):
        return Experiment.objects.filter(team=self.request.team)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form):
        experiment = form.instance
        if experiment.conversational_consent_enabled and not experiment.seed_message:
            messages.error(
                request=self.request, message="A seed message is required when conversational consent is enabled!"
            )
            return render(self.request, self.template_name, self.get_context_data())
        return super().form_valid(form)


class CreateExperiment(BaseExperimentView, CreateView):
    title = "Create Experiment"
    button_title = "Create"
    permission_required = "experiments.add_experiment"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "file_formset" not in context:
            context["file_formset"] = self._get_file_formset()
        return context

    def _get_file_formset(self):
        if flag_is_active(self.request, "experiment_rag"):
            return get_file_formset(self.request)

    def get_initial(self):
        initial = super().get_initial()
        long_data = self.request.session.pop(PROMPT_DATA_SESSION_KEY, None)
        if long_data:
            initial.update(long_data)
        return initial

    def post(self, request, *args, **kwargs):
        self.object = None
        form = self.get_form()
        file_formset = self._get_file_formset()
        if form.is_valid() and (not file_formset or file_formset.is_valid()):
            return self.form_valid(form, file_formset)
        else:
            return self.form_invalid(form, file_formset)

    @transaction.atomic()
    def form_valid(self, form, file_formset):
        self.object = form.save()
        if file_formset:
            files = file_formset.save(self.request)
            self.object.files.set(files)
        return HttpResponseRedirect(self.get_success_url())

    def form_invalid(self, form, file_formset):
        return self.render_to_response(self.get_context_data(form=form, file_formset=file_formset))


class EditExperiment(BaseExperimentView, UpdateView):
    title = "Update Experiment"
    button_title = "Update"
    permission_required = "experiments.change_experiment"

    def get_initial(self):
        initial = super().get_initial()
        initial["type"] = "assistant" if self.object.assistant_id else "llm"
        return initial


def _get_voice_provider_alpine_context(request):
    """Add context required by the experiments/experiment_form.html template."""
    exclude_services = [SyntheticVoice.OpenAIVoiceEngine]
    if flag_is_active(request, "open_ai_voice_engine"):
        exclude_services = []
    return {
        "form_attrs": {"x-data": "experiment", "enctype": "multipart/form-data"},
        # map provider ID to provider type
        "voice_providers_types": dict(request.team.voiceprovider_set.values_list("id", "type")),
        "synthetic_voice_options": sorted(
            [
                {
                    "value": voice.id,
                    "text": str(voice),
                    "type": voice.service.lower(),
                    "provider_id": voice.voice_provider_id,
                }
                for voice in SyntheticVoice.get_for_team(request.team, exclude_services=exclude_services)
            ],
            key=lambda v: v["text"],
        ),
        "llm_providers": request.team.llmprovider_set.all(),
        "llm_options": get_llm_provider_choices(request.team),
    }


@login_and_team_required
@permission_required("experiments.delete_experiment", raise_exception=True)
def delete_experiment(request, team_slug: str, pk: int):
    safety_layer = get_object_or_404(Experiment, id=pk, team=request.team)
    safety_layer.delete()
    messages.success(request, "Experiment Deleted")
    return redirect("experiments:experiments_home", team_slug)


class AddFileToExperiment(BaseAddFileHtmxView):
    @transaction.atomic()
    def form_valid(self, form):
        experiment = get_object_or_404(Experiment, team=self.request.team, pk=self.kwargs["pk"])
        file = super().form_valid(form)
        experiment.files.add(file)
        return file

    def get_delete_url(self, file):
        return reverse("experiments:remove_file", args=[self.request.team.slug, self.kwargs["pk"], file.pk])


class DeleteFileFromExperiment(BaseDeleteFileView):
    pass


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def single_experiment_home(request, team_slug: str, experiment_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    user_sessions = ExperimentSession.objects.with_last_message_created_at().filter(
        participant__user=request.user,
        experiment=experiment,
    )
    channels = experiment.experimentchannel_set.exclude(platform__in=[ChannelPlatform.WEB, ChannelPlatform.API]).all()
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
            "available_tags": experiment.team.tag_set.all(),
            "filter_tags_url": reverse(
                "experiments:sessions-list", kwargs={"team_slug": team_slug, "experiment_id": experiment.id}
            ),
            **_get_events_context(experiment, team_slug),
        },
    )


def _get_events_context(experiment: Experiment, team_slug: str):
    combined_events = []
    static_events = (
        StaticTrigger.objects.filter(experiment=experiment)
        .annotate(
            failure_count=Count(
                Case(When(event_logs__status=EventLogStatusChoices.FAILURE, then=1), output_field=IntegerField())
            )
        )
        .values("id", "experiment_id", "type", "action__action_type", "action__params", "failure_count")
        .all()
    )
    timeout_events = (
        TimeoutTrigger.objects.filter(experiment=experiment)
        .annotate(
            failure_count=Count(
                Case(When(event_logs__status=EventLogStatusChoices.FAILURE, then=1), output_field=IntegerField())
            )
        )
        .values(
            "id",
            "experiment_id",
            "delay",
            "action__action_type",
            "action__params",
            "total_num_triggers",
            "failure_count",
        )
        .all()
    )
    for event in static_events:
        combined_events.append({**event, "team_slug": team_slug})
    for event in timeout_events:
        combined_events.append({**event, "type": "__timeout__", "team_slug": team_slug})
    return {"show_events": len(combined_events) > 0, "events_table": EventsTable(combined_events)}


@login_and_team_required
@permission_required("channels.add_experimentchannel", raise_exception=True)
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
                messages.error(request, format_html("Channel data has errors: " + extra_form.errors.as_text()))
                return redirect("experiments:single_experiment_home", team_slug, experiment_id)

        try:
            ExperimentChannel.check_usage_by_another_experiment(
                platform, identifier=config_data[platform.channel_identifier_key], new_experiment=experiment
            )
        except ChannelAlreadyUtilizedException as exception:
            messages.error(request, exception.html_message)
            return redirect("experiments:single_experiment_home", team_slug, experiment_id)

        form.save(experiment, config_data)
        if extra_form:
            message = extra_form.get_success_message(channel=form.instance)
            if message:
                messages.info(request, message)
    return redirect("experiments:single_experiment_home", team_slug, experiment_id)


@login_and_team_required
def update_delete_channel(request, team_slug: str, experiment_id: int, channel_id: int):
    channel = get_object_or_404(
        ExperimentChannel, id=channel_id, experiment_id=experiment_id, experiment__team__slug=team_slug
    )
    if request.POST.get("action") == "delete":
        if not request.user.has_perm("channels.delete_experimentchannel"):
            raise PermissionDenied

        channel.soft_delete()
        return redirect("experiments:single_experiment_home", team_slug, experiment_id)

    if not request.user.has_perm("channels.change_experimentchannel"):
        raise PermissionDenied

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
                messages.error(request, format_html("Channel data has errors: " + extra_form.errors.as_text()))
                return redirect("experiments:single_experiment_home", team_slug, experiment_id)

        platform = ChannelPlatform(form.cleaned_data["platform"])
        channel_identifier = config_data[platform.channel_identifier_key]
        try:
            ExperimentChannel.check_usage_by_another_experiment(
                platform, identifier=channel_identifier, new_experiment=channel.experiment
            )
        except ChannelAlreadyUtilizedException as exception:
            messages.error(request, exception.html_message)
            return redirect("experiments:single_experiment_home", team_slug, experiment_id)

        form.save(channel.experiment, config_data)
    return redirect("experiments:single_experiment_home", team_slug, experiment_id)


def _start_experiment_session(
    experiment: Experiment,
    experiment_channel: ExperimentChannel,
    participant_identifier: str,
    participant_user: CustomUser | None = None,
    session_status: SessionStatus = SessionStatus.ACTIVE,
) -> ExperimentSession:
    if not participant_identifier and not participant_user:
        raise ValueError("Either participant_identifier or participant_user must be specified!")

    if participant_user and participant_identifier != participant_user.email:
        # This should technically never happen, since we disable the input for logged in users
        raise Exception(f"User {participant_user.email} cannot impersonate participant {participant_identifier}")

    with transaction.atomic():
        try:
            participant = Participant.objects.get(team=experiment.team, identifier=participant_identifier)
            if participant_user and participant.user is None:
                # If a participant becomes a user, we must reconcile the user and participant
                participant.user = participant_user
                participant.save()
        except Participant.DoesNotExist:
            participant = Participant.objects.create(
                user=participant_user,
                identifier=participant_identifier,
                team=experiment.team,
            )
        session = ExperimentSession.objects.create(
            team=experiment.team,
            experiment=experiment,
            llm=experiment.llm,
            experiment_channel=experiment_channel,
            status=session_status,
            participant=participant,
        )
    enqueue_static_triggers.delay(session.id, StaticTriggerType.CONVERSATION_START)
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
def start_authed_web_session(request, team_slug: str, experiment_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    experiment_channel = _ensure_experiment_channel_exists(
        experiment=experiment, platform="web", name=f"{experiment.id}-web"
    )
    session = _start_experiment_session(
        experiment,
        experiment_channel=experiment_channel,
        participant_user=request.user,
        participant_identifier=request.user.email,
    )
    return HttpResponseRedirect(
        reverse("experiments:experiment_chat_session", args=[team_slug, experiment_id, session.id])
    )


def _ensure_experiment_channel_exists(experiment: Experiment, platform: str, name: str) -> ExperimentChannel:
    channel, _created = ExperimentChannel.objects.get_or_create(experiment=experiment, platform=platform, name=name)
    return channel


@login_and_team_required
def experiment_chat_session(request, team_slug: str, experiment_id: int, session_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    session = get_object_or_404(
        ExperimentSession, participant__user=request.user, experiment_id=experiment_id, id=session_id
    )
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
    session = get_object_or_404(ExperimentSession, participant__user=user, experiment_id=experiment_id, id=session_id)
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
    session = get_object_or_404(ExperimentSession, participant__user=user, experiment_id=experiment_id, id=session_id)
    last_message = ChatMessage.objects.filter(chat=session.chat).order_by("-created_at").first()
    progress = Progress(AsyncResult(task_id)).get_info()
    # don't render empty messages
    skip_render = progress["complete"] and progress["success"] and not progress["result"]
    return TemplateResponse(
        request,
        "experiments/chat/chat_message_response.html",
        {
            "experiment": experiment,
            "session": session,
            "task_id": task_id,
            "progress": progress,
            "skip_render": skip_render,
            "last_message_datetime": last_message and quote(last_message.created_at.isoformat()),
        },
    )


def poll_messages(request, team_slug: str, experiment_id: int, session_id: int):
    user = get_real_user_or_none(request.user)
    params = request.GET.dict()
    since_param = params.get("since")
    experiment_session = get_object_or_404(
        ExperimentSession, participant__user=user, experiment_id=experiment_id, id=session_id, team=request.team
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


def start_session_public(request, team_slug: str, experiment_id: str):
    try:
        experiment = get_object_or_404(Experiment, public_id=experiment_id, is_active=True, team=request.team)
    except ValidationError:
        # old links dont have uuids
        raise Http404

    consent = experiment.consent_form
    user = get_real_user_or_none(request.user)
    if request.method == "POST":
        form = ConsentForm(consent, request.POST, initial={"identifier": user.email if user else None})
        if form.is_valid():
            experiment_channel = _ensure_experiment_channel_exists(
                experiment=experiment, platform="web", name=f"{experiment.id}-web"
            )
            if consent.capture_identifier:
                identifier = form.cleaned_data.get("identifier", None)
            else:
                # The identifier field will be disabled, so we must generate one
                identifier = user.email if user else str(uuid.uuid4())

            session = _start_experiment_session(
                experiment,
                experiment_channel=experiment_channel,
                participant_user=user,
                participant_identifier=identifier,
            )
            return _record_consent_and_redirect(request, team_slug, session)

    else:
        form = ConsentForm(
            consent,
            initial={
                "experiment_id": experiment.id,
                "identifier": user.email if user else None,
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
            if ExperimentSession.objects.filter(
                team=request.team,
                experiment=experiment,
                status__in=["setup", "pending"],
                participant__identifier=post_form.cleaned_data["email"],
            ).exists():
                participant_email = post_form.cleaned_data["email"]
                messages.info(request, f"{participant_email} already has a pending invitation.")
            else:
                with transaction.atomic():
                    channel = _ensure_experiment_channel_exists(experiment, platform="web", name=f"{experiment.id}-web")
                    session = _start_experiment_session(
                        experiment=experiment,
                        experiment_channel=channel,
                        participant_identifier=post_form.cleaned_data["email"],
                        session_status=SessionStatus.SETUP,
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
    tags = request.POST["tags"]
    tags = tags.split(",") if tags else []

    # Create a HttpResponse with the CSV data and file attachment headers
    response = HttpResponse(experiment_to_csv(experiment, tags).getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{experiment.name}-export.csv"'
    return response


@login_and_team_required
@permission_required("experiments.invite_participants", raise_exception=True)
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
    if experiment_session.experiment.pre_survey:
        experiment_session.status = SessionStatus.PENDING_PRE_SURVEY
        redirct_url_name = "experiments:experiment_pre_survey"
    else:
        experiment_session.status = SessionStatus.ACTIVE
        redirct_url_name = "experiments:experiment_chat"
    experiment_session.save()
    response = HttpResponseRedirect(
        reverse(
            redirct_url_name,
            args=[team_slug, experiment_session.experiment.public_id, experiment_session.public_id],
        )
    )
    return set_session_access_cookie(response, experiment_session)


@experiment_session_view(allowed_states=[SessionStatus.SETUP, SessionStatus.PENDING])
def start_session_from_invite(request, team_slug: str, experiment_id: str, session_id: str):
    experiment = get_object_or_404(Experiment, public_id=experiment_id, team=request.team)
    experiment_session = get_object_or_404(ExperimentSession, experiment=experiment, public_id=session_id)
    consent = experiment.consent_form

    initial = {
        "experiment_id": experiment.id,
    }
    if not experiment_session.participant:
        raise Http404()

    initial["participant_id"] = experiment_session.participant.id
    initial["identifier"] = experiment_session.participant.identifier

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
@verify_session_access_cookie
def experiment_pre_survey(request, team_slug: str, experiment_id: str, session_id: str):
    if request.method == "POST":
        form = SurveyCompletedForm(request.POST)
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
        form = SurveyCompletedForm()
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
@verify_session_access_cookie
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
@verify_session_access_cookie
@require_POST
def end_experiment(request, team_slug: str, experiment_id: str, session_id: str):
    experiment_session = request.experiment_session
    experiment_session.update_status(SessionStatus.PENDING_REVIEW, commit=False)
    experiment_session.end(commit=True)
    return HttpResponseRedirect(reverse("experiments:experiment_review", args=[team_slug, experiment_id, session_id]))


@experiment_session_view(allowed_states=[SessionStatus.PENDING_REVIEW])
@verify_session_access_cookie
def experiment_review(request, team_slug: str, experiment_id: str, session_id: str):
    form = None
    survey_link = None
    survey_text = None
    if request.method == "POST":
        # no validation needed
        request.experiment_session.status = SessionStatus.COMPLETE
        request.experiment_session.reviewed_at = timezone.now()
        request.experiment_session.save()
        return HttpResponseRedirect(
            reverse("experiments:experiment_complete", args=[team_slug, experiment_id, session_id])
        )
    elif request.experiment.post_survey:
        form = SurveyCompletedForm()
        survey_link = request.experiment_session.get_post_survey_link()
        survey_text = request.experiment.post_survey.confirmation_text.format(survey_link=survey_link)

    return TemplateResponse(
        request,
        "experiments/experiment_review.html",
        {
            "experiment": request.experiment,
            "experiment_session": request.experiment_session,
            "active_tab": "experiments",
            "survey_link": survey_link,
            "survey_text": survey_text,
            "form": form,
            "available_tags": [t.name for t in Tag.objects.filter(team__slug=team_slug).all()],
        },
    )


@experiment_session_view(allowed_states=[SessionStatus.COMPLETE])
@verify_session_access_cookie
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
@verify_session_access_cookie
def experiment_session_details_view(request, team_slug: str, experiment_id: str, session_id: str):
    session = request.experiment_session
    experiment = request.experiment

    return TemplateResponse(
        request,
        "experiments/experiment_session_view.html",
        {
            "experiment": experiment,
            "experiment_session": session,
            "active_tab": "experiments",
            "details": [
                (gettext("Participant"), session.get_participant_display),
                (gettext("Status"), session.get_status_display),
                (gettext("Started"), session.consent_date or session.created_at),
                (gettext("Ended"), session.ended_at or "-"),
                (gettext("Experiment"), experiment.name),
                (gettext("Platform"), session.get_platform_name),
            ],
            "available_tags": [t.name for t in Tag.objects.filter(team__slug=team_slug).all()],
            "event_triggers": [
                {
                    "event_logs": trigger.event_logs.filter(session=session).order_by("-created_at").all(),
                    "trigger": trigger,
                }
                for trigger in experiment.event_triggers
            ],
        },
    )


@experiment_session_view()
@login_and_team_required
def experiment_session_pagination_view(request, team_slug: str, experiment_id: str, session_id: str):
    session = request.experiment_session
    experiment = request.experiment
    query = ExperimentSession.objects.exclude(public_id=session_id).filter(experiment=experiment)
    if request.GET.get("dir", "next") == "next":
        next_session = query.filter(created_at__lte=session.created_at).first()
    else:
        next_session = query.filter(created_at__gte=session.created_at).last()

    if not next_session:
        messages.warning(request, "No more sessions to paginate")
        return redirect("experiments:experiment_session_view", team_slug, experiment_id, session_id)

    return redirect("experiments:experiment_session_view", team_slug, experiment_id, next_session.public_id)
