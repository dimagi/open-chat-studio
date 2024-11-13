import json
import logging
import uuid
from datetime import datetime
from urllib.parse import quote

import jwt
from celery.result import AsyncResult
from celery_progress.backend import Progress
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, When
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
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
from field_audit.models import AuditAction
from waffle import flag_is_active

from apps.annotations.models import Tag
from apps.channels.exceptions import ExperimentChannelException
from apps.channels.forms import ChannelForm
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import WebChannel
from apps.chat.models import ChatAttachment, ChatMessage, ChatMessageType
from apps.custom_actions.utils import (
    clean_custom_action_operations,
    initialize_form_for_custom_actions,
    set_custom_actions,
)
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
from apps.experiments.export import experiment_to_csv
from apps.experiments.forms import (
    ConsentForm,
    ExperimentInvitationForm,
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
from apps.experiments.tasks import get_response_for_webchat_task
from apps.experiments.views.prompt import PROMPT_DATA_SESSION_KEY
from apps.files.forms import get_file_formset
from apps.files.models import File
from apps.files.views import BaseAddFileHtmxView, BaseDeleteFileView
from apps.service_providers.utils import get_llm_provider_choices
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.prompt import PromptVars, validate_prompt_variables


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
        query_set = Experiment.objects.filter(team=self.request.team, working_version__isnull=True, is_archived=False)
        search = self.request.GET.get("search")
        if search:
            search_vector = SearchVector("name", weight="A") + SearchVector("description", weight="B")
            search_query = SearchQuery(search)
            query_set = (
                query_set.annotate(document=search_vector, rank=SearchRank(search_vector, search_query))
                .filter(Q(document=search_query) | Q(owner__username__icontains=search))
                .order_by("-rank")
            )
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
        if not self.request.GET.get("show-all"):
            query_set = query_set.exclude(experiment_channel__platform=ChannelPlatform.API)

        if tags_query := self.request.GET.get("tags"):
            tags = tags_query.split("&")
            query_set = query_set.filter(chat__tags__name__in=tags).distinct()

        if participant := self.request.GET.get("participant"):
            query_set = query_set.filter(participant__identifier=participant)
        return query_set


class ExperimentVersionsTableView(SingleTableView, PermissionRequiredMixin):
    model = Experiment
    paginate_by = 25
    table_class = ExperimentVersionsTable
    template_name = "experiments/experiment_version_table.html"
    permission_required = "experiments.view_experiment"

    def get_queryset(self):
        return (
            Experiment.objects.filter(working_version=self.kwargs["experiment_id"], is_archived=False)
            .order_by("-version_number")
            .all()
        )


class ExperimentForm(forms.ModelForm):
    PROMPT_HELP_TEXT = """
        <div class="tooltip" data-tip="
            Available variables to include in your prompt: {source_material}, {participant_data}, and
            {current_datetime}.
            {source_material} should be included when there is source material linked to the experiment.
            {participant_data} is optional.
            {current_datetime} is only required when the bot is using a tool.
        ">
            <i class="text-xs fa fa-circle-question">
            </i>
        </div>
    """
    type = forms.ChoiceField(
        choices=[
            ("llm", gettext("Base Language Model")),
            ("assistant", gettext("OpenAI Assistant")),
            ("pipeline", gettext("Pipeline")),
        ],
        widget=forms.RadioSelect(attrs={"x-model": "type"}),
    )
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    prompt_text = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}), required=False, help_text=PROMPT_HELP_TEXT)
    input_formatter = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    seed_message = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    tools = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, choices=AgentTools.choices, required=False)
    custom_action_operations = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, required=False)

    class Meta:
        model = Experiment
        fields = [
            "name",
            "description",
            "llm_provider",
            "llm_provider_model",
            "assistant",
            "pipeline",
            "temperature",
            "prompt_text",
            "input_formatter",
            "safety_layers",
            "conversational_consent_enabled",
            "source_material",
            "seed_message",
            "pre_survey",
            "post_survey",
            "consent_form",
            "voice_provider",
            "synthetic_voice",
            "safety_violation_notification_emails",
            "voice_response_behaviour",
            "tools",
            "echo_transcript",
            "use_processor_bot_voice",
            "trace_provider",
            "participant_allowlist",
            "debug_mode_enabled",
            "citations_enabled",
        ]
        labels = {"source_material": "Inline Source Material", "participant_allowlist": "Participant allowlist"}
        help_texts = {
            "source_material": "Use the '{source_material}' tag to inject source material directly into your prompt.",
            "assistant": "If you have an OpenAI assistant, you can select it here to use it for this experiment.",
            "use_processor_bot_voice": (
                "In a multi-bot setup, use the configured voice of the bot that generated the output. If it doesn't "
                "have one, the router bot's voice will be used."
            ),
            "participant_allowlist": (
                "Separate identifiers with a comma. Phone numbers should be in E164 format e.g. +27123456789"
            ),
            "debug_mode_enabled": (
                "Enabling this tags each AI message in the web UI with the bot responsible for generating it. "
                "This is applicable only for router bots."
            ),
            "citations_enabled": "Whether to include cited sources in responses",
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
        self.fields["assistant"].queryset = team.openaiassistant_set.exclude(is_version=True)
        self.fields["pipeline"].queryset = team.pipeline_set
        self.fields["voice_provider"].queryset = team.voiceprovider_set.exclude(
            syntheticvoice__service__in=exclude_services
        )
        self.fields["safety_layers"].queryset = team.safetylayer_set.exclude(is_version=True)
        self.fields["source_material"].queryset = team.sourcematerial_set.exclude(is_version=True)
        self.fields["pre_survey"].queryset = team.survey_set.exclude(is_version=True)
        self.fields["post_survey"].queryset = team.survey_set.exclude(is_version=True)
        self.fields["consent_form"].queryset = team.consentform_set.exclude(is_version=True)
        self.fields["synthetic_voice"].queryset = SyntheticVoice.get_for_team(team, exclude_services)
        self.fields["trace_provider"].queryset = team.traceprovider_set
        initialize_form_for_custom_actions(team, self)

        # Alpine.js bindings
        self.fields["voice_provider"].widget.attrs = {
            "x-model.fill": "voiceProvider",
        }
        self.fields["llm_provider"].widget.attrs = {
            "x-model.number.fill": "llmProviderId",
        }
        # special template for dynamic select options
        self.fields["synthetic_voice"].widget.template_name = "django/forms/widgets/select_dynamic.html"
        self.fields["llm_provider_model"].widget.template_name = "django/forms/widgets/select_dynamic.html"

    def clean_participant_allowlist(self):
        cleaned_identifiers = []
        for identifier in self.cleaned_data["participant_allowlist"]:
            cleaned_identifiers.append(identifier.replace(" ", ""))
        return cleaned_identifiers

    def clean_custom_action_operations(self):
        return clean_custom_action_operations(self)

    def clean(self):
        cleaned_data = super().clean()

        errors = {}
        bot_type = cleaned_data["type"]
        if bot_type == "llm":
            cleaned_data["assistant"] = None
            cleaned_data["pipeline"] = None
            if not cleaned_data.get("prompt_text"):
                errors["prompt_text"] = "Prompt text is required unless you select an OpenAI Assistant"
            if not cleaned_data.get("llm_provider"):
                errors["llm_provider"] = "LLM Provider is required unless you select an OpenAI Assistant"
            if not cleaned_data.get("llm_provider_model"):
                errors["llm_provider_model"] = "LLM Model is required unless you select an OpenAI Assistant"
            if cleaned_data.get("llm_provider") and cleaned_data.get("llm_provider_model"):
                if not cleaned_data["llm_provider"].type == cleaned_data["llm_provider_model"].type:
                    errors[
                        "llm_provider_model"
                    ] = "You must select a provider model that is the same type as the provider"

        elif bot_type == "assistant":
            cleaned_data["pipeline"] = None
            if not cleaned_data.get("assistant"):
                errors["assistant"] = "Assistant is required when creating an assistant experiment"
        elif bot_type == "pipeline":
            cleaned_data["assistant"] = None
            if not cleaned_data.get("pipeline"):
                errors["pipeline"] = "Pipeline is required when creating a pipeline experiment"

        if errors:
            raise forms.ValidationError(errors)

        validate_prompt_variables(
            form_data=cleaned_data,
            prompt_key="prompt_text",
            known_vars=set(PromptVars.values),
        )
        return cleaned_data

    def save(self, commit=True):
        experiment = super().save(commit=False)
        experiment.team = self.request.team
        experiment.owner = self.request.user
        if commit:
            experiment.save()
            set_custom_actions(experiment, self.cleaned_data.get("custom_action_operations"))
            self.save_m2m()
        return experiment


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
        if self.object:
            team_participant_identifiers.extend(self.object.participant_allowlist)
            team_participant_identifiers = set(team_participant_identifiers)

        return {
            **{
                "title": self.title,
                "button_text": self.button_title,
                "active_tab": "experiments",
                "experiment_type": experiment_type,
                "available_tools": AgentTools.choices,
                "team_participant_identifiers": team_participant_identifiers,
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

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if obj.working_version:
            raise Http404("Cannot edit experiment versions.")
        return obj


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
    return HttpResponse(headers={"HX-Redirect": reverse("experiments:experiments_home", args=[team_slug])})


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


# TODO: complete form
class ExperimentVersionForm(forms.ModelForm):
    class Meta:
        model = Experiment
        fields = ["version_description", "is_default_version"]
        help_texts = {"version_description": "A description of this version, or what changed from the previous version"}


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
        version = working_experiment.version
        if prev_version := working_experiment.latest_version:
            # Populate diffs
            version.compare(prev_version.version)

        context["version_details"] = version
        return context

    def form_valid(self, form):
        working_experiment = self.get_object()
        description = form.cleaned_data["version_description"]
        is_default = form.cleaned_data["is_default_version"]
        working_experiment.create_new_version(version_description=description, make_default=is_default)
        return HttpResponseRedirect(self.get_success_url())

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
def single_experiment_home(request, team_slug: str, experiment_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    user_sessions = (
        ExperimentSession.objects.with_last_message_created_at()
        .filter(
            participant__user=request.user,
            experiment=experiment,
        )
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
            "available_tags": experiment.team.tag_set.filter(is_system_tag=False),
            "deployed_version": deployed_version,
            **_get_events_context(experiment, team_slug),
            **_get_routes_context(experiment, team_slug),
            **_get_terminal_bots_context(experiment, team_slug),
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
def experiment_chat_session(request, team_slug: str, experiment_id: int, session_id: int, version_number: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    session = get_object_or_404(
        ExperimentSession, participant__user=request.user, experiment_id=experiment_id, id=session_id
    )
    try:
        experiment_version = experiment.get_version(version_number)
    except Experiment.DoesNotExist:
        raise Http404

    version_specific_vars = {
        "assistant": experiment_version.assistant,
        "experiment_name": experiment_version.name,
        "experiment_version_number": version_number,
    }
    return TemplateResponse(
        request,
        "experiments/experiment_chat.html",
        {"experiment": experiment, "session": session, "active_tab": "experiments", **version_specific_vars},
    )


@experiment_session_view()
@verify_session_access_cookie
@require_POST
def experiment_session_message(request, team_slug: str, experiment_id: int, session_id: int, version_number: int):
    working_experiment = request.experiment
    session = request.experiment_session

    try:
        experiment_version = working_experiment.get_version(version_number)
    except Experiment.DoesNotExist:
        raise Http404

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
            attachments.append({"type": resource_type, "file_id": new_file.id})
            created_files.append(new_file)

        tool_resource.files.add(*created_files)

    result = get_response_for_webchat_task.delay(
        experiment_session_id=session.id,
        experiment_id=experiment_version.id,
        message_text=message_text,
        attachments=attachments,
    )
    version_specific_vars = {
        "assistant": experiment_version.assistant,
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
            **version_specific_vars,
        },
    )


@experiment_session_view()
@verify_session_access_cookie
def get_message_response(request, team_slug: str, experiment_id: str, session_id: str, task_id: str):
    experiment = request.experiment
    session = request.experiment_session
    last_message = ChatMessage.objects.filter(chat=session.chat).order_by("-created_at").first()
    progress = Progress(AsyncResult(task_id)).get_info()
    # don't render empty messages
    skip_render = progress["complete"] and progress["success"] and not progress["result"]

    message_details = {"message": None, "error": False, "complete": progress["complete"]}
    if progress["complete"] and progress["success"]:
        result = progress["result"]
        if result["message_id"]:
            message_details["message"] = ChatMessage.objects.get(id=result["message_id"])
        else:
            message_details["message"] = {"content": result["response"]}
    elif progress["complete"]:
        message_details["error"] = True

    return TemplateResponse(
        request,
        "experiments/chat/chat_message_response.html",
        {
            "experiment": experiment,
            "session": session,
            "task_id": task_id,
            "message_details": message_details,
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

    since = timezone.now()
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
        experiment = get_object_or_404(Experiment, public_id=experiment_id, team=request.team)
    except ValidationError:
        # old links dont have uuids
        raise Http404

    experiment_version = experiment.default_version
    if not experiment_version.is_public:
        raise Http404

    consent = experiment_version.consent_form
    user = get_real_user_or_none(request.user)
    if request.method == "POST":
        form = ConsentForm(consent, request.POST, initial={"identifier": user.email if user else None})
        if form.is_valid():
            verify_user = True
            if consent.capture_identifier:
                identifier = form.cleaned_data.get("identifier", None)
            else:
                # The identifier field will be disabled, so we must generate one
                identifier = user.email if user else str(uuid.uuid4())
                verify_user = False

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
                    session=session,
                )
            else:
                return _record_consent_and_redirect(request, team_slug, session)
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


def _verify_user_or_start_session(identifier, request, session):
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
        return _record_consent_and_redirect(request, team_slug, session)

    if session_data := get_chat_session_access_cookie_data(request, fail_silently=True):
        if Participant.objects.filter(
            id=session_data["participant_id"], identifier=identifier, team_id=session.team_id
        ).exists():
            return _record_consent_and_redirect(request, team_slug, session)

    send_chat_link_email(session)
    return TemplateResponse(request=request, template="account/participant_email_verify.html")


def verify_public_chat_token(request, team_slug: str, experiment_id: str, token: str):
    try:
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms="HS256")
        session = get_object_or_404(ExperimentSession, external_id=claims["session"])
        return _record_consent_and_redirect(request, team_slug, session)
    except Exception:
        messages.warning(request=request, message="This link could not be verified")
        return redirect(reverse("experiments:start_session_public", args=(team_slug, experiment_id)))


@login_and_team_required
@permission_required("experiments.invite_participants", raise_exception=True)
def experiment_invitations(request, team_slug: str, experiment_id: str):
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
    return TemplateResponse(
        request,
        "experiments/experiment_invitations.html",
        {"invitation_form": form, "experiment": experiment, "sessions": sessions, **version_specific_vars},
    )


@require_POST
@permission_required("experiments.download_chats", raise_exception=True)
def download_experiment_chats(request, team_slug: str, experiment_id: str):
    # todo: this could be made more efficient and should be async, but just shipping something for now
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    tags = request.POST["tags"]
    tags = tags.split(",") if tags else []

    participant = request.POST.get("participant")

    # Create a HttpResponse with the CSV data and file attachment headers
    response = HttpResponse(experiment_to_csv(experiment, tags, participant).getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{experiment.name}-export.csv"'
    return response


@login_and_team_required
@permission_required("experiments.invite_participants", raise_exception=True)
def send_invitation(request, team_slug: str, experiment_id: str, session_id: str):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    session = ExperimentSession.objects.get(experiment=experiment, external_id=session_id)
    send_experiment_invitation(session)
    return TemplateResponse(
        request,
        "experiments/manage/invite_row.html",
        context={"request": request, "experiment": experiment, "session": session},
    )


def _record_consent_and_redirect(request, team_slug: str, experiment_session: ExperimentSession):
    # record consent, update status
    experiment_session.consent_date = timezone.now()
    if experiment_session.experiment_version.pre_survey:
        experiment_session.status = SessionStatus.PENDING_PRE_SURVEY
        redirect_url_name = "experiments:experiment_pre_survey"
    else:
        experiment_session.status = SessionStatus.ACTIVE
        redirect_url_name = "experiments:experiment_chat"
    experiment_session.save()
    response = HttpResponseRedirect(
        reverse(
            redirect_url_name,
            args=[team_slug, experiment_session.experiment.public_id, experiment_session.external_id],
        )
    )
    return set_session_access_cookie(response, experiment_session)


@experiment_session_view(allowed_states=[SessionStatus.SETUP, SessionStatus.PENDING])
def start_session_from_invite(request, team_slug: str, experiment_id: str, session_id: str):
    experiment = get_object_or_404(Experiment, public_id=experiment_id, team=request.team)
    experiment_session = get_object_or_404(ExperimentSession, experiment=experiment, external_id=session_id)
    default_version = experiment.default_version
    consent = default_version.consent_form

    initial = {
        "participant_id": experiment_session.participant.id,
        "identifier": experiment_session.participant.identifier,
    }
    if not experiment_session.participant:
        raise Http404()

    if request.method == "POST":
        form = ConsentForm(consent, request.POST, initial=initial)
        if form.is_valid():
            return _record_consent_and_redirect(request, team_slug, experiment_session)

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
def experiment_chat(request, team_slug: str, experiment_id: str, session_id: str):
    version_specific_vars = {
        "experiment_name": request.experiment.default_version.name,
    }
    return TemplateResponse(
        request,
        "experiments/experiment_chat.html",
        {
            "experiment": request.experiment,
            "session": request.experiment_session,
            "active_tab": "experiments",
            **version_specific_vars,
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
                (gettext("Participant"), session.get_participant_chip()),
                (gettext("Status"), session.get_status_display),
                (gettext("Started"), session.consent_date or session.created_at),
                (gettext("Ended"), session.ended_at or "-"),
                (gettext("Experiment"), experiment.name),
                (gettext("Platform"), session.get_platform_name),
            ],
            "available_tags": [t.name for t in Tag.objects.filter(team__slug=team_slug, is_system_tag=False).all()],
            "event_triggers": [
                {
                    "event_logs": trigger.event_logs.filter(session=session).order_by("-created_at").all(),
                    "trigger": trigger,
                }
                for trigger in experiment.event_triggers
            ],
            "participant_data": json.dumps(session.participant_data_from_experiment, indent=4),
            "participant_schedules": session.participant.get_schedules_for_experiment(
                experiment, as_dict=True, include_complete=True
            ),
        },
    )


@experiment_session_view()
@login_and_team_required
def experiment_session_pagination_view(request, team_slug: str, experiment_id: str, session_id: str):
    session = request.experiment_session
    experiment = request.experiment
    query = ExperimentSession.objects.exclude(external_id=session_id).filter(experiment=experiment)
    if request.GET.get("dir", "next") == "next":
        next_session = query.filter(created_at__lte=session.created_at).first()
    else:
        next_session = query.filter(created_at__gte=session.created_at).last()

    if not next_session:
        messages.warning(request, "No more sessions to paginate")
        return redirect("experiments:experiment_session_view", team_slug, experiment_id, session_id)

    return redirect("experiments:experiment_session_view", team_slug, experiment_id, next_session.external_id)


@login_and_team_required
@permission_required("chat.view_chatattachment")
def download_file(request, team_slug: str, session_id: int, pk: int):
    resource = get_object_or_404(
        File, id=pk, team__slug=team_slug, chatattachment__chat__experiment_session__id=session_id
    )
    try:
        file = resource.file.open()
        return FileResponse(file, as_attachment=True, filename=resource.file.name)
    except FileNotFoundError:
        raise Http404()


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
def archive_experiment(request, team_slug: str, experiment_id: int, version_number: int):
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
    experiment.is_archived = True
    experiment.save()
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
    experiment_version = get_object_or_404(
        Experiment, working_version_id=experiment_id, version_number=version_number, team=request.team
    )

    context = {"version_details": experiment_version.version, "experiment": experiment_version}
    return render(request, "experiments/components/experiment_version_details_content.html", context)
