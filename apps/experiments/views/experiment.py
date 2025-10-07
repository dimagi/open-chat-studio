import logging
import unicodedata
import uuid
from datetime import datetime
from functools import cached_property
from typing import cast

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Prefetch, Q, When
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, UpdateView
from django.views.generic.edit import FormView
from django_tables2 import SingleTableView
from waffle import flag_is_active

from apps.analysis.const import LANGUAGE_CHOICES
from apps.annotations.models import CustomTaggedItem
from apps.assistants.sync import OpenAiSyncError, get_diff_with_openai_assistant, get_out_of_sync_files
from apps.channels.datamodels import Attachment, AttachmentType
from apps.chat.channels import WebChannel
from apps.chat.models import ChatAttachment, ChatMessage, ChatMessageType
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
from apps.experiments.email import send_chat_link_email
from apps.experiments.filters import (
    ExperimentSessionFilter,
    get_filter_context_data,
)
from apps.experiments.forms import (
    ExperimentForm,
    ExperimentVersionForm,
)
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
from apps.experiments.tasks import (
    async_create_experiment_version,
    get_response_for_webchat_task,
)
from apps.experiments.views.prompt import PROMPT_DATA_SESSION_KEY
from apps.experiments.views.utils import get_channels_context
from apps.files.models import File
from apps.generics.chips import Chip
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.service_providers.utils import get_llm_provider_choices
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.utils.base_experiment_table_view import BaseExperimentTableView
from apps.web.dynamic_filters.datastructures import FilterParams


class ExperimentTableView(BaseExperimentTableView):
    model = Experiment
    table_class = ExperimentTable
    permission_required = "experiments.view_experiment"


class ExperimentSessionsTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    """
    This view is used to render experiment sessions. When called by a specific chatbot, it includes an "experiment_id"
    parameter in the request, which narrows the sessions to only those belonging to that chatbot.
    """

    model = ExperimentSession
    paginate_by = 25
    table_class = ExperimentSessionsTable
    template_name = "table/single_table.html"
    permission_required = "experiments.view_experimentsession"

    def get_queryset(self):
        experiment_filter = Q()
        if experiment_id := self.kwargs.get("experiment_id"):
            experiment_filter = Q(experiment__id=experiment_id)

        query_set = (
            ExperimentSession.objects.with_last_message_created_at()
            .filter(experiment_filter, team=self.request.team)
            .select_related("participant__user", "chat")
            .prefetch_related(
                "chat__tags",
                "chat__messages__tags",
                Prefetch(
                    "chat__tagged_items",
                    queryset=CustomTaggedItem.objects.select_related("tag", "user"),
                    to_attr="prefetched_tagged_items",
                ),
            )
        )
        timezone = self.request.session.get("detected_tz", None)

        session_filter = ExperimentSessionFilter()
        query_set = session_filter.apply(
            query_set, filter_params=FilterParams.from_request(self.request), timezone=timezone
        )
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

    def get_initial(self):
        initial = super().get_initial()
        long_data = self.request.session.pop(PROMPT_DATA_SESSION_KEY, None)
        if long_data:
            initial.update(long_data)
        return initial

    def form_valid(self, form):
        with transaction.atomic():
            form.instance.name = unicodedata.normalize("NFC", form.instance.name)
            self.object = form.save()

        task_id = async_create_experiment_version.delay(
            experiment_id=self.object.id, version_description="", make_default=True
        )
        self.object.create_version_task_id = task_id
        self.object.save(update_fields=["create_version_task_id"])

        return HttpResponseRedirect(self.get_success_url())

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form))

    def dispatch(self, request, *args, **kwargs):
        is_chatbot = kwargs.get("new_chatbot", False)
        if not is_chatbot:
            return HttpResponseRedirect(reverse("chatbots:new", args=[request.team.slug]))
        return super().dispatch(request, *args, **kwargs)


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
    if flag_is_active(request, "flag_open_ai_voice_engine"):
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


class CreateExperimentVersion(LoginAndTeamRequiredMixin, FormView, PermissionRequiredMixin):
    model = Experiment
    form_class = ExperimentVersionForm
    template_name = "experiments/create_version_form.html"
    title = "Create Experiment Version"
    button_title = "Create"
    permission_required = "experiments.add_experiment"

    @cached_property
    def object(self):
        return get_object_or_404(Experiment, pk=self.kwargs["experiment_id"], team=self.request.team)

    @cached_property
    def latest_version(self):
        return self.object.latest_version

    def get_form_kwargs(self) -> dict:
        form_kwargs = super().get_form_kwargs()
        if not self.latest_version:
            form_kwargs["initial"] = {"is_default_version": True}
        return form_kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        working_experiment = self.object
        version = working_experiment.version_details
        if self.latest_version:
            # Populate diffs
            version.compare(self.latest_version.version_details)

        context["version_details"] = version
        context["has_versions"] = self.latest_version is not None
        context["experiment"] = working_experiment
        return context

    def form_valid(self, form):
        description = form.cleaned_data["version_description"]
        is_default = form.cleaned_data["is_default_version"]
        working_version = self.object

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
        experiment = self.object

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


def base_single_experiment_view(request, team_slug, experiment_id, template_name, active_tab) -> HttpResponse:
    experiment = get_object_or_404(Experiment.objects.get_all(), id=experiment_id, team=request.team)

    channels, available_platforms = get_channels_context(experiment)

    deployed_version = None
    if experiment != experiment.default_version:
        deployed_version = experiment.default_version.version_number

    bot_type_chip = None
    if active_tab == "experiments":
        if pipeline := experiment.pipeline:
            bot_type_chip = Chip(label=f"Pipeline: {pipeline.name}", url=pipeline.get_absolute_url())
        elif assistant := experiment.assistant:
            bot_type_chip = Chip(label=f"Assistant: {assistant.name}", url=assistant.get_absolute_url())

    context = {
        "active_tab": active_tab,
        "bot_type_chip": bot_type_chip,
        "experiment": experiment,
        "platforms": available_platforms,
        "channels": channels,
        "deployed_version": deployed_version,
        "allow_copy": not experiment.child_links.exists(),
        **_get_events_context(experiment, team_slug, request.origin),
    }
    if active_tab != "chatbots":
        context.update(**_get_terminal_bots_context(experiment, team_slug))
        context.update(**_get_routes_context(experiment, team_slug))
        session_table_url = reverse("experiments:sessions-list", args=(team_slug, experiment_id))
    else:
        session_table_url = reverse("chatbots:sessions-list", args=(team_slug, experiment_id))

    columns = ExperimentSessionFilter.columns(request.team, single_experiment=experiment)
    context.update(get_filter_context_data(request.team, columns, "last_message", session_table_url, "sessions-table"))

    return TemplateResponse(request, template_name, context)


def _get_events_context(experiment: Experiment, team_slug: str, origin=None):
    combined_events = []
    static_events = (
        StaticTrigger.objects.filter(experiment=experiment)
        .annotate(
            failure_count=Count(
                Case(When(event_logs__status=EventLogStatusChoices.FAILURE, then=1), output_field=IntegerField())
            )
        )
        .values("id", "experiment_id", "type", "action__action_type", "action__params", "failure_count", "is_active")
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
            "is_active",
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
        "first_parent_id": parent_links[0].parent_id if parent_links else None,
        "can_make_child_routes": len(parent_links) == 0,
    }


def _get_terminal_bots_context(experiment: Experiment, team_slug: str):
    return {
        "terminal_bots_table": TerminalBotsTable(
            experiment.child_links.filter(type=ExperimentRouteType.TERMINAL).all()
        ),
    }


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
    for resource_type in ["code_interpreter", "file_search", "ocs_attachments"]:
        if resource_type not in uploaded_files:
            continue

        tool_resource, _created = ChatAttachment.objects.get_or_create(
            chat_id=session.chat_id,
            tool_type=resource_type,
        )
        for uploaded_file in uploaded_files.getlist(resource_type):
            new_file = File.objects.create(name=uploaded_file.name, file=uploaded_file, team=request.team)
            attachments.append(Attachment.from_file(new_file, cast(AttachmentType, resource_type), session.id))
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


def _record_consent_and_redirect(
    team_slug: str,
    experiment: Experiment,
    experiment_session: ExperimentSession,
):
    # record consent, update status
    experiment_session.consent_date = timezone.now()
    if experiment_session.experiment_version.pre_survey:
        experiment_session.status = SessionStatus.PENDING_PRE_SURVEY
        redirect_url_name = "experiments:experiment_pre_survey"
    else:
        experiment_session.status = SessionStatus.ACTIVE
        redirect_url_name = "chatbots:chatbot_chat"
    experiment_session.save()
    response = HttpResponseRedirect(
        reverse(
            redirect_url_name,
            args=[team_slug, experiment_session.experiment.public_id, experiment_session.external_id],
        )
    )
    return set_session_access_cookie(response, experiment, experiment_session)


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


def _get_languages_for_chat(session):
    available_language_codes = session.chat.translated_languages
    available_languages = [
        choice for choice in LANGUAGE_CHOICES if choice[0] == "" or choice[0] in available_language_codes
    ]
    translatable_languages = [
        choice for choice in LANGUAGE_CHOICES if choice[0] != "" and choice[0] not in available_language_codes
    ]
    return available_languages, translatable_languages


@experiment_session_view()
@verify_session_access_cookie
def translate_messages_view(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    from apps.analysis.translation import translate_messages_with_llm

    session = request.experiment_session
    provider_id = request.POST.get("llm_provider", "")
    model_id = request.POST.get("llm_provider_model", "")
    valid_languages = [choice[0] for choice in LANGUAGE_CHOICES if choice[0]]
    translate_all = request.POST.get("translate_all", "false") == "true"
    if translate_all:
        language = request.POST.get("target_language")
    else:
        language = request.POST.get("language")

    if not language or language not in valid_languages:
        messages.error(request, "No language selected for translation.")
        return redirect_to_messages_view(request, session)
    if not provider_id or not model_id:
        messages.error(request, "No LLM provider model selected.")
        return redirect_to_messages_view(request, session)
    try:
        try:
            llm_provider = LlmProvider.objects.get(id=provider_id, team=request.team)
            llm_provider_model = LlmProviderModel.objects.get(id=model_id)
        except (LlmProvider.DoesNotExist, LlmProviderModel.DoesNotExist):
            messages.error(request, "Selected provider or model not found.")
            return redirect_to_messages_view(request, session)

        messages_to_translate = ChatMessage.objects.filter(chat=session.chat).exclude(
            **{f"translations__{language}__isnull": False}
        )
        if not messages_to_translate.exists():
            messages.info(request, "All messages already have translations for this language.")
            return redirect_to_messages_view(request, session)
        translate_messages_with_llm(
            messages=list(messages_to_translate),
            target_language=language,
            llm_provider=llm_provider,
            llm_provider_model=llm_provider_model,
        )
    except Exception as e:
        logging.exception("Error translating messages")
        messages.error(request, f"Translation failed: {str(e)}")
        return redirect_to_messages_view(request, session)

    return redirect_to_messages_view(request, session)


def redirect_to_messages_view(request, session):
    url = reverse(
        "experiments:experiment_session_messages_view",
        args=[request.team.slug, session.experiment.public_id, session.external_id],
    )
    params = {}
    search = request.POST.get("search", "").strip()
    show_original_translation = request.POST.get("show_original_translation", "")
    language = request.POST.get("language", "")
    params["language"] = language or request.POST.get("target_language", "")
    if search:
        params["search"] = search
    if show_original_translation:
        params["show_original_translation"] = show_original_translation

    if params:
        from urllib.parse import urlencode

        url += "?" + urlencode(params)

    return HttpResponseRedirect(url)


@login_and_team_required
def get_release_status_badge(request, team_slug: str, experiment_id: int):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    context = {"has_changes": experiment.compare_with_latest(), "experiment": experiment}
    return render(request, "experiments/components/unreleased_badge.html", context)


@login_and_team_required
@permission_required(("experiments.change_experiment", "pipelines.add_pipeline"))
def migrate_experiment_view(request, team_slug, experiment_id):
    from apps.pipelines.helper import convert_non_pipeline_experiment_to_pipeline

    experiment = get_object_or_404(Experiment, id=experiment_id, team__slug=team_slug)
    failed_url = reverse(
        "experiments:single_experiment_home",
        kwargs={"team_slug": team_slug, "experiment_id": experiment_id},
    )
    if experiment.parent_links.exists():
        messages.error(
            request, "Child experiments will be migrated along with their 'parent'. Please migrate the parent."
        )
        return redirect(failed_url)

    try:
        with transaction.atomic():
            experiment = Experiment.objects.get(id=experiment_id)
            convert_non_pipeline_experiment_to_pipeline(experiment)
        messages.success(request, f'Successfully migrated experiment "{experiment.name}" to chatbot!')
        return redirect("chatbots:single_chatbot_home", team_slug=team_slug, experiment_id=experiment_id)
    except Exception:
        logging.exception(
            "Failed to migrate experiment to chatbot", details={"team_slug": team_slug, "experiment_id": experiment_id}
        )
        messages.error(request, "There was an error during the migration. Please try again later.")
        return redirect(failed_url)
