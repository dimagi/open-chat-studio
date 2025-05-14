import logging
import unicodedata
import uuid
from datetime import datetime
from typing import cast
from urllib.parse import parse_qs, urlparse

import jwt
from celery.result import AsyncResult
from celery_progress.backend import Progress
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, Count, IntegerField, When
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, UpdateView
from django_tables2 import SingleTableView
from field_audit.models import AuditAction
from waffle import flag_is_active

from apps.annotations.models import Tag
from apps.assistants.sync import OpenAiSyncError, get_diff_with_openai_assistant, get_out_of_sync_files
from apps.channels.datamodels import Attachment, AttachmentType
from apps.channels.exceptions import ExperimentChannelException
from apps.channels.forms import ChannelForm
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import WebChannel
from apps.chat.models import Chat, ChatAttachment, ChatMessage, ChatMessageType
from apps.events.models import (
    EventLogStatusChoices,
    StaticTrigger,
    TimeoutTrigger,
)
from apps.events.tables import (
    EventsTable,
)
from apps.experiments.decorators import (
    experiment_session_view,
    get_chat_session_access_cookie_data,
    set_session_access_cookie,
    verify_session_access_cookie,
)
from apps.experiments.email import send_chat_link_email, send_experiment_invitation
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.experiments.filters import FIELD_TYPE_FILTERS, apply_dynamic_filters
from apps.experiments.forms import (
    ConsentForm,
    ExperimentForm,
    ExperimentInvitationForm,
    ExperimentVersionForm,
    SurveyCompletedForm,
)
from apps.experiments.helpers import get_real_user_or_none
from apps.experiments.models import (
    AgentTools,
    Experiment,
    ExperimentRoute,
    ExperimentRouteType,
    ExperimentSession,
    Participant,
    SessionStatus,
    SyntheticVoice,
)
from apps.experiments.tables import (
    ChildExperimentRoutesTable,
    ExperimentSessionsTable,
    ExperimentTable,
    ExperimentVersionsTable,
    ParentExperimentRoutesTable,
    TerminalBotsTable,
)
from apps.experiments.tasks import async_create_experiment_version, async_export_chat, get_response_for_webchat_task
from apps.experiments.views.prompt import PROMPT_DATA_SESSION_KEY
from apps.files.forms import get_file_formset
from apps.files.models import File
from apps.files.views import BaseAddFileHtmxView, BaseDeleteFileView
from apps.generics.chips import Chip
from apps.generics.views import generic_home, paginate_session, render_session_details
from apps.service_providers.utils import get_llm_provider_choices
from apps.teams.decorators import login_and_team_required, team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.base_experiment_table_view import BaseExperimentTableView

DEFAULT_ERROR_MESSAGE = (
    "Sorry something went wrong. This was likely an intermittent error related to load."
    "Please try again, and wait a few minutes if this keeps happening."
)

CUSTOM_ERROR_MESSAGE = (
    "The chatbot is currently unavailable. We are working hard to resolve the issue as quickly"
    " as possible and apologize for any inconvenience. Thank you for your patience."
)


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def experiments_home(request, team_slug: str):
    return generic_home(request, team_slug, "Experiments", "experiments:table", "experiments:new")


class ExperimentTableView(BaseExperimentTableView):
    model = Experiment
    table_class = ExperimentTable
    permission_required = "experiments.view_experiment"


class ExperimentSessionsTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    model = ExperimentSession
    paginate_by = 25
    table_class = ExperimentSessionsTable
    template_name = "table/single_table.html"
    permission_required = "experiments.view_experimentsession"

    def get_queryset(self):
        query_set = (
            ExperimentSession.objects.with_last_message_created_at()
            .filter(team=self.request.team, experiment__id=self.kwargs["experiment_id"])
            .select_related("participant__user")
        )
        query_set = apply_dynamic_filters(query_set, self.request)
        return query_set


class ExperimentVersionsTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    model = Experiment
    paginate_by = 25
    table_class = ExperimentVersionsTable
    template_name = "experiments/experiment_version_table.html"
    permission_required = "experiments.view_experiment"

    def get_queryset(self):
        experiment_row = Experiment.objects.get_all().filter(id=self.kwargs["experiment_id"])
        other_versions = Experiment.objects.get_all().filter(working_version=self.kwargs["experiment_id"]).all()
        return (experiment_row | other_versions).order_by("-version_number")


class BaseExperimentView(LoginAndTeamRequiredMixin, PermissionRequiredMixin):
    model = Experiment
    template_name = "experiments/experiment_form.html"
    form_class = ExperimentForm

    @property
    def extra_context(self):
        if self.object and self.object.assistant_id:
            experiment_type = "assistant"
        elif self.object and self.object.pipeline_id:
            experiment_type = "pipeline"
        else:
            experiment_type = "llm"
        if self.request.POST.get("type"):
            experiment_type = self.request.POST.get("type")

        team_participant_identifiers = list(
            self.request.team.participant_set.filter(user=None).values_list("identifier", flat=True)
        )
        disable_version_button = False
        if self.object:
            team_participant_identifiers.extend(self.object.participant_allowlist)
            team_participant_identifiers = set(team_participant_identifiers)
            disable_version_button = self.object.create_version_task_id

        return {
            **{
                "title": self.title,
                "button_text": self.button_title,
                "active_tab": "experiments",
                "experiment_type": experiment_type,
                "available_tools": AgentTools.user_tool_choices(),
                "team_participant_identifiers": team_participant_identifiers,
                "disable_version_button": disable_version_button,
            },
            **_get_voice_provider_alpine_context(self.request),
        }

    def get_success_url(self):
        return reverse("experiments:single_experiment_home", args=[self.request.team.slug, self.object.pk])

    def get_queryset(self):
        return Experiment.objects.get_all().filter(team=self.request.team)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form):
        experiment = form.instance
        if experiment.assistant and ExperimentRoute.objects.filter(parent=experiment):
            messages.error(
                request=self.request, message="Assistants cannot be routers. Please remove the routes first."
            )
            return render(self.request, self.template_name, self.get_context_data())

        if experiment.conversational_consent_enabled and not experiment.seed_message:
            messages.error(
                request=self.request, message="A seed message is required when conversational consent is enabled!"
            )
            return render(self.request, self.template_name, self.get_context_data())
        response = super().form_valid(form)

        if self.request.POST.get("action") == "save_and_version":
            return redirect("experiments:create_version", self.request.team.slug, experiment.id)

        if self.request.POST.get("action") == "save_and_archive":
            experiment = get_object_or_404(Experiment, id=experiment.id, team=self.request.team)
            experiment.archive()
            return redirect("experiments:experiments_home", self.request.team.slug)
        return response


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

    def form_valid(self, form, file_formset):
        with transaction.atomic():
            form.instance.name = unicodedata.normalize("NFC", form.instance.name)
            self.object = form.save()
            if file_formset:
                files = file_formset.save(self.request)
                self.object.files.set(files)

        task_id = async_create_experiment_version.delay(
            experiment_id=self.object.id, version_description="", make_default=True
        )
        self.object.create_version_task_id = task_id
        self.object.save(update_fields=["create_version_task_id"])

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

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if obj.working_version:
            raise Http404("Experiment not found.")
        return obj

    def post(self, request, *args, **kwargs):
        if self.get_object().is_archived:
            raise PermissionDenied("Cannot edit archived experiments.")
        return super().post(request, *args, **kwargs)


def _get_voice_provider_alpine_context(request):
    """Add context required by the experiments/experiment_form.html template."""
    exclude_services = [SyntheticVoice.OpenAIVoiceEngine]
    if flag_is_active(request, "open_ai_voice_engine"):
        exclude_services = []

    form_attrs = {"enctype": "multipart/form-data"}
    if request.origin == "experiments":
        form_attrs["x-data"] = "experiment"

    return {
        "form_attrs": form_attrs,
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
    return redirect("experiments:experiments_home", team_slug=team_slug)


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


class CreateExperimentVersion(LoginAndTeamRequiredMixin, CreateView):
    model = Experiment
    form_class = ExperimentVersionForm
    template_name = "experiments/create_version_form.html"
    title = "Create Experiment Version"
    button_title = "Create"
    permission_required = "experiments.add_experiment"
    pk_url_kwarg = "experiment_id"

    def get_form_kwargs(self) -> dict:
        form_kwargs = super().get_form_kwargs()
        experiment = self.get_object()
        if not experiment.has_versions:
            form_kwargs["initial"] = {"is_default_version": True}
        return form_kwargs

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        working_experiment = self.get_object()
        version = working_experiment.version_details
        if prev_version := working_experiment.latest_version:
            # Populate diffs
            version.compare(prev_version.version_details)

        context["version_details"] = version
        context["experiment"] = working_experiment
        return context

    def form_valid(self, form):
        description = form.cleaned_data["version_description"]
        is_default = form.cleaned_data["is_default_version"]
        working_version = Experiment.objects.get(id=self.kwargs["experiment_id"])

        if working_version.is_archived:
            raise PermissionDenied("Unable to version an archived experiment.")

        if working_version.create_version_task_id:
            messages.error(self.request, "Version creation is already in progress.")
            return HttpResponseRedirect(self.get_success_url())

        error_msg = self._check_pipleline_and_assistant_for_errors()

        if error_msg:
            messages.error(self.request, error_msg)
            return render(self.request, self.template_name, self.get_context_data(form=form))

        task_id = async_create_experiment_version.delay(
            experiment_id=working_version.id, version_description=description, make_default=is_default
        )
        working_version.create_version_task_id = task_id
        working_version.save(update_fields=["create_version_task_id"])
        messages.success(self.request, "Creating new version. This might take a few minutes.")

        return HttpResponseRedirect(self.get_success_url())

    def _check_pipleline_and_assistant_for_errors(self) -> str:
        """Checks if the pipeline or assistant has errors before creating a new version."""
        experiment = self.get_object()

        try:
            if self._is_assistant_out_of_sync(experiment):
                return "Assistant is out of sync with OpenAI. Please update the assistant first."
        except OpenAiSyncError as e:
            return str(e)

        if pipeline := experiment.pipeline:
            errors = pipeline.validate()
            if errors:
                return "Unable to create a new version when the pipeline has errors"

    def _is_assistant_out_of_sync(self, experiment: Experiment) -> bool:
        if not experiment.assistant:
            return False

        if not experiment.assistant.assistant_id:
            return True

        if len(get_diff_with_openai_assistant(experiment.assistant)) > 0:
            return True

        files_missing_local, files_missing_remote = get_out_of_sync_files(experiment.assistant)
        return bool(files_missing_local or files_missing_remote)

    def get_success_url(self):
        url = reverse(
            "experiments:single_experiment_home",
            kwargs={
                "team_slug": self.request.team.slug,
                "experiment_id": self.kwargs["experiment_id"],
            },
        )
        return f"{url}#versions"


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def version_create_status(request, team_slug: str, experiment_id: int):
    experiment = Experiment.objects.get(id=experiment_id, team=request.team)
    return TemplateResponse(
        request,
        "experiments/create_version_button.html",
        {
            "active_tab": "experiments",
            "experiment": experiment,
            "trigger_refresh": experiment.create_version_task_id is not None,
        },
    )


def base_single_experiment_view(request, team_slug, experiment_id, template_name, active_tab) -> HttpResponse:
    experiment = get_object_or_404(Experiment.objects.get_all(), id=experiment_id, team=request.team)

    user_sessions = (
        ExperimentSession.objects.with_last_message_created_at()
        .filter(participant__user=request.user, experiment=experiment)
        .exclude(experiment_channel__platform=ChannelPlatform.API)
    )
    channels = experiment.experimentchannel_set.exclude(platform__in=[ChannelPlatform.WEB, ChannelPlatform.API]).all()
    used_platforms = {channel.platform_enum for channel in channels}
    available_platforms = ChannelPlatform.for_dropdown(used_platforms, experiment.team)
    platform_forms = {}
    form_kwargs = {"experiment": experiment}
    for platform in available_platforms:
        if platform.form(**form_kwargs):
            platform_forms[platform] = platform.form(**form_kwargs)

    deployed_version = None
    if experiment != experiment.default_version:
        deployed_version = experiment.default_version.version_number

    bot_type_chip = None
    if active_tab == "experiments":
        if pipeline := experiment.pipeline:
            bot_type_chip = Chip(label=f"Pipeline: {pipeline.name}", url=pipeline.get_absolute_url())
        elif assistant := experiment.assistant:
            bot_type_chip = Chip(label=f"Assistant: {assistant.name}", url=assistant.get_absolute_url())

    channel_list = ChannelPlatform.for_filter(experiment.team)
    context = {
        "active_tab": active_tab,
        "bot_type_chip": bot_type_chip,
        "experiment": experiment,
        "user_sessions": user_sessions,
        "platforms": available_platforms,
        "platform_forms": platform_forms,
        "channels": channels,
        "available_tags": [tag.name for tag in experiment.team.tag_set.filter(is_system_tag=False)],
        "experiment_versions": experiment.get_version_name_list(),
        "deployed_version": deployed_version,
        "field_type_filters": FIELD_TYPE_FILTERS,
        "channel_list": channel_list,
        "allow_copy": not experiment.child_links.exists(),
        **_get_events_context(experiment, team_slug, request.origin),
    }
    if active_tab != "chatbots":
        context.update(**_get_terminal_bots_context(experiment, team_slug))
        context.update(**_get_routes_context(experiment, team_slug))

    return TemplateResponse(request, template_name, context)


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def single_experiment_home(request, team_slug: str, experiment_id: int):
    return base_single_experiment_view(
        request, team_slug, experiment_id, "experiments/single_experiment_home.html", "experiments"
    )


def _get_events_context(experiment: Experiment, team_slug: str, origin=None):
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
    return {"show_events": len(combined_events) > 0, "events_table": EventsTable(combined_events, origin=origin)}


def _get_routes_context(experiment: Experiment, team_slug: str):
    route_type = ExperimentRouteType.PROCESSOR
    parent_links = experiment.parent_links.filter(type=route_type).all()
    return {
        "child_routes_table": ChildExperimentRoutesTable(experiment.child_links.filter(type=route_type).all()),
        "parent_routes_table": ParentExperimentRoutesTable(parent_links),
        "can_make_child_routes": len(parent_links) == 0,
    }


def _get_terminal_bots_context(experiment: Experiment, team_slug: str):
    return {
        "terminal_bots_table": TerminalBotsTable(
            experiment.child_links.filter(type=ExperimentRouteType.TERMINAL).all()
        ),
    }


@login_and_team_required
@permission_required("channels.add_experimentchannel", raise_exception=True)
def create_channel(request, team_slug: str, experiment_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    existing_platforms = {channel.platform_enum for channel in experiment.experimentchannel_set.all()}
    form = ChannelForm(experiment=experiment, data=request.POST)
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
                messages.error(request, format_html("Channel data has errors: " + extra_form.errors.as_ul()))
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
            try:
                extra_form.post_save(channel=form.instance)
            except ExperimentChannelException as e:
                messages.error(request, "Error saving channel: " + str(e))
            else:
                if extra_form.success_message:
                    messages.info(request, extra_form.success_message)

                if extra_form.warning_message:
                    messages.warning(request, extra_form.warning_message)
    return redirect("experiments:single_experiment_home", team_slug, experiment_id)


@login_and_team_required
def update_delete_channel(request, team_slug: str, experiment_id: int, channel_id: int):
    channel = get_object_or_404(ExperimentChannel, id=channel_id, experiment_id=experiment_id, team__slug=team_slug)
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
                messages.error(request, format_html("Channel data has errors: " + extra_form.errors.as_ul()))
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


@require_POST
@login_and_team_required
def start_authed_web_session(request, team_slug: str, experiment_id: int, version_number: int):
    """Start an authed web session with the chosen experiment, be it a specific version or not"""
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)

    session = WebChannel.start_new_session(
        working_experiment=experiment,
        participant_user=request.user,
        participant_identifier=request.user.email,
        timezone=request.session.get("detected_tz", None),
        version=version_number,
    )
    return HttpResponseRedirect(
        reverse("experiments:experiment_chat_session", args=[team_slug, experiment_id, version_number, session.id])
    )


@login_and_team_required
def experiment_chat_session(
    request, team_slug: str, experiment_id: int, session_id: int, version_number: int, active_tab: str = "experiments"
):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    session = get_object_or_404(
        ExperimentSession, participant__user=request.user, experiment_id=experiment_id, id=session_id
    )
    try:
        experiment_version = experiment.get_version(version_number)
    except Experiment.DoesNotExist:
        raise Http404() from None

    version_specific_vars = {
        "assistant": experiment_version.get_assistant(),
        "experiment_name": experiment_version.name,
        "experiment_version": experiment_version,
        "experiment_version_number": experiment_version.version_number,
    }
    return TemplateResponse(
        request,
        "experiments/experiment_chat.html",
        {"experiment": experiment, "session": session, "active_tab": active_tab, **version_specific_vars},
    )


@experiment_session_view()
@verify_session_access_cookie
@require_POST
def experiment_session_message(request, team_slug: str, experiment_id: uuid.UUID, session_id: str, version_number: int):
    return _experiment_session_message(request, version_number)


@experiment_session_view()
@require_POST
@xframe_options_exempt
@csrf_exempt
def experiment_session_message_embed(
    request, team_slug: str, experiment_id: uuid.UUID, session_id: str, version_number: int
):
    if not request.experiment_session.participant.is_anonymous:
        return HttpResponseForbidden()

    return _experiment_session_message(request, version_number, embedded=True)


def _experiment_session_message(request, version_number: int, embedded=False):
    working_experiment = request.experiment
    session = request.experiment_session

    if working_experiment.is_archived:
        raise PermissionDenied("Cannot chat with an archived experiment.")

    try:
        experiment_version = working_experiment.get_version(version_number)
    except Experiment.DoesNotExist:
        raise Http404() from None

    message_text = request.POST["message"]
    uploaded_files = request.FILES
    attachments = []
    created_files = []
    for resource_type in ["code_interpreter", "file_search"]:
        if resource_type not in uploaded_files:
            continue

        tool_resource, _created = ChatAttachment.objects.get_or_create(
            chat_id=session.chat_id,
            tool_type=resource_type,
        )
        for uploaded_file in uploaded_files.getlist(resource_type):
            new_file = File.objects.create(name=uploaded_file.name, file=uploaded_file, team=request.team)
            attachments.append(Attachment.from_file(new_file, cast(AttachmentType, resource_type)))
            created_files.append(new_file)

        tool_resource.files.add(*created_files)

    if attachments and not message_text:
        message_text = "Please look at the attachments and respond appropriately"

    result = get_response_for_webchat_task.delay(
        experiment_session_id=session.id,
        experiment_id=experiment_version.id,
        message_text=message_text,
        attachments=[att.model_dump() for att in attachments],
    )
    version_specific_vars = {
        "assistant": experiment_version.get_assistant(),
        "experiment_version_number": experiment_version.version_number,
    }
    return TemplateResponse(
        request,
        "experiments/chat/experiment_response_htmx.html",
        {
            "experiment": working_experiment,
            "session": session,
            "message_text": message_text,
            "task_id": result.task_id,
            "created_files": created_files,
            "embedded": embedded,
            **version_specific_vars,
        },
    )


@experiment_session_view()
def get_message_response(request, team_slug: str, experiment_id: uuid.UUID, session_id: str, task_id: str):
    experiment = request.experiment
    session = request.experiment_session
    last_message = ChatMessage.objects.filter(chat=session.chat).order_by("-created_at").first()
    progress = Progress(AsyncResult(task_id)).get_info()
    # don't render empty messages
    skip_render = progress["complete"] and progress["success"] and not progress["result"]
    if skip_render:
        return HttpResponse()

    message_details = {"message": None, "error_msg": False, "complete": progress["complete"]}
    if progress["complete"] and progress["success"]:
        result = progress["result"]
        if message_id := result.get("message_id"):
            message_details["message"] = ChatMessage.objects.get(id=message_id)
        elif response := result.get("response"):
            message_details["message"] = {"content": response}
        if error := result.get("error"):
            if not experiment.debug_mode_enabled:
                if "Invalid parameter" in error:  # TODO: temporary
                    message_details["error_msg"] = CUSTOM_ERROR_MESSAGE
                else:
                    message_details["error_msg"] = DEFAULT_ERROR_MESSAGE
            else:
                message_details["error_msg"] = error
    elif progress["complete"]:
        message_details["error_msg"] = DEFAULT_ERROR_MESSAGE

    attached_files = []
    message = message_details.get("message")
    if isinstance(message, ChatMessage):
        attached_files = message.get_attached_files()

    return TemplateResponse(
        request,
        "experiments/chat/chat_message_response.html",
        {
            "experiment": experiment,
            "session": session,
            "task_id": task_id,
            "message_details": message_details,
            "skip_render": skip_render,
            "last_message_datetime": last_message and last_message.created_at,
            "attachments": attached_files,
        },
    )


@experiment_session_view()
@require_GET
@xframe_options_exempt
@team_required
def poll_messages_embed(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    if not request.experiment_session.participant.is_anonymous:
        return HttpResponseForbidden()

    return _poll_messages(request)


@experiment_session_view()
@require_GET
@team_required
def poll_messages(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    user = get_real_user_or_none(request.user)
    if user and request.experiment_session.participant.user != user:
        return HttpResponseForbidden()

    return _poll_messages(request)


def _poll_messages(request):
    params = request.GET.dict()
    since_param = params.get("since")

    since = timezone.now()
    if since_param and since_param != "null":
        try:
            since = datetime.fromisoformat(since_param)
        except ValueError as e:
            logging.exception(f"Unexpected `since` parameter value. Error: {e}")

    messages = (
        ChatMessage.objects.filter(
            message_type=ChatMessageType.AI, chat=request.experiment_session.chat, created_at__gt=since
        )
        .order_by("created_at")
        .all()
    )

    if messages:
        return TemplateResponse(
            request,
            "experiments/chat/system_message.html",
            {
                "messages": [message.content for message in messages],
                "last_message_datetime": messages[0].created_at,
            },
        )
    return HttpResponse()


@team_required
def start_session_public(request, team_slug: str, experiment_id: uuid.UUID):
    try:
        experiment = get_object_or_404(Experiment, public_id=experiment_id, team=request.team)
    except ValidationError:
        # old links dont have uuids
        raise Http404() from None

    experiment_version = experiment.default_version
    if not experiment_version.is_public:
        raise Http404

    consent = experiment_version.consent_form
    user = get_real_user_or_none(request.user)
    if not consent:
        identifier = user.email if user else str(uuid.uuid4())
        session = WebChannel.start_new_session(
            working_experiment=experiment,
            participant_user=user,
            participant_identifier=identifier,
            timezone=request.session.get("detected_tz", None),
        )
        return _record_consent_and_redirect(team_slug, experiment, session, request.origin)

    if request.method == "POST":
        form = ConsentForm(consent, request.POST, initial={"identifier": user.email if user else None})
        if form.is_valid():
            verify_user = True
            if consent.capture_identifier:
                identifier = form.cleaned_data.get("identifier", None)
            else:
                # The identifier field will be disabled, so we must generate one
                verify_user = False
                if user:
                    identifier = user.email
                else:
                    identifier = Participant.create_anonymous(request.team, ChannelPlatform.WEB).identifier

            session = WebChannel.start_new_session(
                working_experiment=experiment,
                participant_user=user,
                participant_identifier=identifier,
                timezone=request.session.get("detected_tz", None),
            )
            if verify_user and consent.identifier_type == "email":
                return _verify_user_or_start_session(
                    identifier=identifier,
                    request=request,
                    experiment=experiment,
                    session=session,
                )
            else:
                return _record_consent_and_redirect(team_slug, experiment, session)
    else:
        form = ConsentForm(
            consent,
            initial={
                "experiment_id": experiment_version.id,
                "identifier": user.email if user else None,
            },
        )

    consent_notice = consent.get_rendered_content()
    version_specific_vars = {
        "experiment_name": experiment_version.name,
        "experiment_description": experiment_version.description,
    }
    return TemplateResponse(
        request,
        "experiments/start_experiment_session.html",
        {
            "active_tab": "experiments",
            "experiment": experiment,
            "consent_notice": mark_safe(consent_notice),
            "form": form,
            **version_specific_vars,
        },
    )


@xframe_options_exempt
@team_required
def start_session_public_embed(request, team_slug: str, experiment_id: uuid.UUID):
    """Special view for starting sessions from embedded widgets. This will ignore consent and pre-surveys and
    will ALWAYS create anonymous participants."""
    try:
        experiment = get_object_or_404(Experiment, public_id=experiment_id, team=request.team)
    except ValidationError:
        # old links dont have uuids
        raise Http404() from None

    experiment_version = experiment.default_version
    if not experiment_version.is_public:
        raise Http404

    participant = Participant.create_anonymous(request.team, ChannelPlatform.WEB)
    session = WebChannel.start_new_session(
        working_experiment=experiment,
        participant_identifier=participant.identifier,
        timezone=request.session.get("detected_tz", None),
        metadata={Chat.MetadataKeys.EMBED_SOURCE: request.headers.get("referer", None)},
    )
    redirect_url = (
        "chatbots:chatbot_chat_embed" if request.origin == "chatbots" else "experiments:experiment_chat_embed"
    )
    return redirect(redirect_url, team_slug, experiment.public_id, session.external_id)


def _verify_user_or_start_session(identifier, request, experiment, session):
    """
    Verifies if the user is allowed to access the chat.

    Process:
    1. If the user is currently logged in, they are considered verified.
    2. If not logged in, check for a session cookie from a prior public chat:
        - The session cookie should contain a `participant_id` field.
        - Match the specified `identifier` to the one of the participant from the session cookie.
        - If the identifiers match, the user previously verified their email and can proceed.
    3. If there is no match or if the session has expired, the user has to verify their email address.
    """
    team_slug = session.team.slug
    if request.user.is_authenticated:
        return _record_consent_and_redirect(team_slug, experiment, session)

    if not session.requires_participant_data():
        return _record_consent_and_redirect(team_slug, experiment, session)

    if session_data := get_chat_session_access_cookie_data(request, fail_silently=True):
        if Participant.objects.filter(
            id=session_data["participant_id"], identifier=identifier, team_id=session.team_id
        ).exists():
            return _record_consent_and_redirect(team_slug, experiment, session)

    token_expiry: datetime = send_chat_link_email(session)
    return TemplateResponse(
        request=request, template="account/participant_email_verify.html", context={"token_expiry": token_expiry}
    )


@team_required
def verify_public_chat_token(request, team_slug: str, experiment_id: uuid.UUID, token: str):
    try:
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms="HS256")
        session = ExperimentSession.objects.select_related("experiment").get(external_id=claims["session"])
        return _record_consent_and_redirect(team_slug, session.experiment, session)
    except jwt.exceptions.ExpiredSignatureError:
        messages.warning(request=request, message="This link has expired")
        return redirect(reverse("experiments:start_session_public", args=(team_slug, experiment_id)))
    except Exception:
        messages.warning(request=request, message="This link could not be verified")
        return redirect(reverse("experiments:start_session_public", args=(team_slug, experiment_id)))


@login_and_team_required
@permission_required("experiments.invite_participants", raise_exception=True)
def experiment_invitations(request, team_slug: str, experiment_id: int, origin="experiments"):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    experiment_version = experiment.default_version
    sessions = experiment.sessions.order_by("-created_at").filter(
        status__in=["setup", "pending"],
        participant__isnull=False,
    )
    form = ExperimentInvitationForm(initial={"experiment_id": experiment_id})
    if request.method == "POST":
        post_form = ExperimentInvitationForm(request.POST)
        if post_form.is_valid():
            if ExperimentSession.objects.filter(
                team=request.team,
                experiment_id=experiment_id,
                status__in=["setup", "pending"],
                participant__identifier=post_form.cleaned_data["email"],
            ).exists():
                participant_email = post_form.cleaned_data["email"]
                messages.info(request, f"{participant_email} already has a pending invitation.")
            else:
                with transaction.atomic():
                    session = WebChannel.start_new_session(
                        experiment,
                        participant_identifier=post_form.cleaned_data["email"],
                        session_status=SessionStatus.SETUP,
                        timezone=request.session.get("detected_tz", None),
                    )
                if post_form.cleaned_data["invite_now"]:
                    send_experiment_invitation(session)
        else:
            form = post_form

    version_specific_vars = {
        "experiment_name": experiment_version.name,
        "experiment_description": experiment_version.description,
    }
    template_name = (
        "chatbots/chatbot_invitations.html" if origin == "chatbots" else "experiments/experiment_invitations.html"
    )
    return TemplateResponse(
        request,
        template_name,
        {"invitation_form": form, "experiment": experiment, "sessions": sessions, **version_specific_vars},
    )


@require_POST
@permission_required("experiments.download_chats", raise_exception=True)
@login_and_team_required
def generate_chat_export(request, team_slug: str, experiment_id: str):
    experiment = get_object_or_404(Experiment, id=experiment_id)
    parsed_url = urlparse(request.headers.get("HX-Current-URL"))
    query_params = parse_qs(parsed_url.query)
    task_id = async_export_chat.delay(experiment_id, query_params)
    return TemplateResponse(
        request, "experiments/components/exports.html", {"experiment": experiment, "task_id": task_id}
    )


@permission_required("experiments.download_chats", raise_exception=True)
@login_and_team_required
def get_export_download_link(request, team_slug: str, experiment_id: str, task_id: str):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    info = Progress(AsyncResult(task_id)).get_info()
    context = {"experiment": experiment}
    if info["complete"] and info["success"]:
        file_id = info["result"]["file_id"]
        download_url = reverse("files:base", kwargs={"team_slug": team_slug, "pk": file_id})
        context["export_download_url"] = download_url
    else:
        context["task_id"] = task_id
    return TemplateResponse(request, "experiments/components/exports.html", context)


@login_and_team_required
@permission_required("experiments.invite_participants", raise_exception=True)
def send_invitation(request, team_slug: str, experiment_id: int, session_id: str):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    session = ExperimentSession.objects.get(experiment=experiment, external_id=session_id)
    send_experiment_invitation(session)
    return TemplateResponse(
        request,
        "experiments/manage/invite_row.html",
        context={"request": request, "experiment": experiment, "session": session},
    )


def _record_consent_and_redirect(
    team_slug: str, experiment: Experiment, experiment_session: ExperimentSession, origin="experiments"
):
    # record consent, update status
    experiment_session.consent_date = timezone.now()
    if experiment_session.experiment_version.pre_survey:
        experiment_session.status = SessionStatus.PENDING_PRE_SURVEY
        redirect_url_name = "experiments:experiment_pre_survey"
    else:
        experiment_session.status = SessionStatus.ACTIVE
        redirect_url_name = "chatbots:chatbot_chat" if origin == "chatbots" else "experiments:experiment_chat"
    experiment_session.save()
    response = HttpResponseRedirect(
        reverse(
            redirect_url_name,
            args=[team_slug, experiment_session.experiment.public_id, experiment_session.external_id],
        )
    )
    return set_session_access_cookie(response, experiment, experiment_session)


@experiment_session_view(allowed_states=[SessionStatus.SETUP, SessionStatus.PENDING])
def start_session_from_invite(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    default_version = request.experiment.default_version
    consent = default_version.consent_form

    initial = {
        "participant_id": request.experiment_session.participant.id,
        "identifier": request.experiment_session.participant.identifier,
    }
    if not request.experiment_session.participant:
        raise Http404()

    if not consent:
        return _record_consent_and_redirect(team_slug, request.experiment, request.experiment_session)

    if request.method == "POST":
        form = ConsentForm(consent, request.POST, initial=initial)
        if form.is_valid():
            return _record_consent_and_redirect(team_slug, request.experiment, request.experiment_session)

    else:
        form = ConsentForm(consent, initial=initial)

    consent_notice = consent.get_rendered_content()
    version_specific_vars = {
        "experiment_name": default_version.name,
        "experiment_description": default_version.description,
    }
    return TemplateResponse(
        request,
        "experiments/start_experiment_session.html",
        {
            "active_tab": "experiments",
            "experiment": default_version,
            "consent_notice": mark_safe(consent_notice),
            "form": form,
            **version_specific_vars,
        },
    )


@experiment_session_view(allowed_states=[SessionStatus.PENDING_PRE_SURVEY])
@verify_session_access_cookie
def experiment_pre_survey(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
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

    default_version = request.experiment.default_version
    experiment_session = request.experiment_session
    version_specific_vars = {
        "experiment_name": default_version.name,
        "experiment_description": default_version.description,
        "pre_survey_link": experiment_session.get_pre_survey_link(default_version),
    }
    return TemplateResponse(
        request,
        "experiments/pre_survey.html",
        {
            "active_tab": "experiments",
            "form": form,
            "experiment": request.experiment,
            "experiment_session": experiment_session,
            **version_specific_vars,
        },
    )


@experiment_session_view(allowed_states=[SessionStatus.ACTIVE, SessionStatus.SETUP])
@verify_session_access_cookie
def experiment_chat(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    return _experiment_chat_ui(request)


@experiment_session_view(allowed_states=[SessionStatus.ACTIVE, SessionStatus.SETUP])
@xframe_options_exempt
def experiment_chat_embed(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    """Special view for embedding that doesn't have the cookie security. This is OK because of the additional
    checks to ensure the participant is 'anonymous'."""
    session = request.experiment_session
    if not session.participant.is_anonymous:
        raise Http404
    return _experiment_chat_ui(request, embedded=True)


def _experiment_chat_ui(request, embedded=False):
    experiment_version = request.experiment.default_version
    version_specific_vars = {
        "assistant": experiment_version.get_assistant(),
        "experiment_name": experiment_version.name,
        "experiment_version": experiment_version,
        "experiment_version_number": experiment_version.version_number,
    }
    return TemplateResponse(
        request,
        "experiments/experiment_chat.html",
        {
            "experiment": request.experiment,
            "session": request.experiment_session,
            "active_tab": "chatbots" if request.origin == "chatbots" else "experiments",
            "embedded": embedded,
            **version_specific_vars,
        },
    )


@experiment_session_view()
@verify_session_access_cookie
def experiment_session_messages_view(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    """View for loading paginated messages with HTMX"""
    session = request.experiment_session
    experiment = request.experiment
    page = int(request.GET.get("page", 1))
    search = request.GET.get("search", "")
    page_size = 100
    messages_queryset = ChatMessage.objects.filter(chat=session.chat).all().order_by("created_at")
    if search:
        messages_queryset = messages_queryset.filter(tags__name__icontains=search).distinct()

    total_messages = messages_queryset.count()
    total_pages = max(1, (total_messages + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paginated_messages = messages_queryset[start_idx:end_idx]
    context = {
        "experiment_session": session,
        "experiment": experiment,
        "messages": paginated_messages,
        "page": page,
        "total_pages": total_pages,
        "total_messages": total_messages,
        "page_size": page_size,
        "page_start_index": start_idx,
        "search": search,
        "available_tags": [t.name for t in Tag.objects.filter(team__slug=team_slug, is_system_tag=False).all()],
    }

    return TemplateResponse(
        request,
        "experiments/components/experiment_chat.html",
        context,
    )


@experiment_session_view(allowed_states=[SessionStatus.ACTIVE, SessionStatus.SETUP])
@verify_session_access_cookie
@require_POST
def end_experiment(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    experiment_session = request.experiment_session
    experiment_session.update_status(SessionStatus.PENDING_REVIEW, commit=False)
    experiment_session.end(commit=True)
    return HttpResponseRedirect(reverse("experiments:experiment_review", args=[team_slug, experiment_id, session_id]))


@experiment_session_view(allowed_states=[SessionStatus.PENDING_REVIEW])
@verify_session_access_cookie
def experiment_review(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    form = None
    survey_link = None
    survey_text = None
    experiment_version = request.experiment.default_version
    if request.method == "POST":
        # no validation needed
        request.experiment_session.status = SessionStatus.COMPLETE
        request.experiment_session.reviewed_at = timezone.now()
        request.experiment_session.save()
        return HttpResponseRedirect(
            reverse("experiments:experiment_complete", args=[team_slug, experiment_id, session_id])
        )
    elif experiment_version.post_survey:
        form = SurveyCompletedForm()
        survey_link = request.experiment_session.get_post_survey_link(experiment_version)
        survey_text = experiment_version.post_survey.confirmation_text.format(survey_link=survey_link)

    version_specific_vars = {
        "experiment.post_survey": experiment_version.post_survey,
        "survey_link": survey_link,
        "survey_text": survey_text,
        "experiment_name": experiment_version.name,
    }
    return TemplateResponse(
        request,
        "experiments/experiment_review.html",
        {
            "experiment": request.experiment,
            "experiment_session": request.experiment_session,
            "active_tab": "experiments",
            "form": form,
            "available_tags": [t.name for t in Tag.objects.filter(team__slug=team_slug, is_system_tag=False).all()],
            **version_specific_vars,
        },
    )


@experiment_session_view(allowed_states=[SessionStatus.COMPLETE])
@verify_session_access_cookie
def experiment_complete(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
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
def experiment_session_details_view(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    return render_session_details(
        request,
        team_slug,
        experiment_id,
        session_id,
        active_tab="experiments",
        template_path="experiments/experiment_session_view.html",
    )


@login_and_team_required
def experiment_session_pagination_view(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    return paginate_session(
        request,
        team_slug,
        experiment_id,
        session_id,
        view_name="experiments:experiment_session_view",
    )


@team_required
def download_file(request, team_slug: str, session_id: int, pk: int):
    resource = get_object_or_404(
        File, id=pk, team__slug=team_slug, chatattachment__chat__experiment_session__id=session_id
    )
    try:
        file = resource.file.open()
        return FileResponse(file, as_attachment=True, filename=resource.file.name)
    except FileNotFoundError:
        raise Http404() from None


@require_POST
@transaction.atomic
@login_and_team_required
def set_default_experiment(request, team_slug: str, experiment_id: int, version_number: int):
    experiment = get_object_or_404(
        Experiment, working_version_id=experiment_id, version_number=version_number, team=request.team
    )
    Experiment.objects.exclude(version_number=version_number).filter(
        team__slug=team_slug, working_version_id=experiment_id
    ).update(is_default_version=False, audit_action=AuditAction.AUDIT)
    experiment.is_default_version = True
    experiment.save()
    url = (
        reverse(
            "experiments:single_experiment_home",
            kwargs={"team_slug": request.team.slug, "experiment_id": experiment_id},
        )
        + "#versions"
    )
    return redirect(url)


@require_POST
@transaction.atomic
@login_and_team_required
def archive_experiment_version(request, team_slug: str, experiment_id: int, version_number: int):
    """
    Archives a single released version of an experiment, unless it's the default version
    """
    experiment = get_object_or_404(
        Experiment, working_version_id=experiment_id, version_number=version_number, team=request.team
    )
    url = (
        reverse(
            "experiments:single_experiment_home",
            kwargs={"team_slug": request.team.slug, "experiment_id": experiment_id},
        )
        + "#versions"
    )
    if experiment.is_default_version:
        return redirect(url)
    experiment.archive()
    return redirect(url)


@require_POST
@transaction.atomic
@login_and_team_required
def update_version_description(request, team_slug: str, experiment_id: int, version_number: int):
    experiment = get_object_or_404(
        Experiment, working_version_id=experiment_id, version_number=version_number, team=request.team
    )
    experiment.version_description = request.POST.get("description", "").strip()
    experiment.save()

    return HttpResponse()


@login_and_team_required
def experiment_version_details(request, team_slug: str, experiment_id: int, version_number: int):
    try:
        experiment_version = Experiment.objects.get_all().get(
            team=request.team, working_version_id=experiment_id, version_number=version_number
        )
    except Experiment.DoesNotExist:
        raise Http404() from None

    context = {"version_details": experiment_version.version_details, "experiment": experiment_version}
    return render(request, "experiments/components/experiment_version_details_content.html", context)


@login_and_team_required
def get_release_status_badge(request, team_slug: str, experiment_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    context = {"has_changes": experiment.compare_with_latest(), "experiment": experiment}
    return render(request, "experiments/components/unreleased_badge.html", context)
