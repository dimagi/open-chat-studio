import unicodedata
import uuid
from functools import cached_property

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, DateTimeField, F, IntegerField, OuterRef, Q, Subquery
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, FormView, TemplateView
from django_htmx.http import HttpResponseClientRedirect
from django_tables2 import SingleTableView
from waffle import flag_is_active

from apps.channels.models import ChannelPlatform
from apps.chat.channels import WebChannel
from apps.chat.models import Chat
from apps.chatbots.forms import ChatbotForm, ChatbotSettingsForm, CopyChatbotForm
from apps.chatbots.tables import ChatbotSessionsTable, ChatbotTable
from apps.experiments.decorators import experiment_session_view, verify_session_access_cookie
from apps.experiments.filters import (
    ExperimentSessionFilter,
    get_filter_context_data,
)
from apps.experiments.forms import ExperimentVersionForm
from apps.experiments.models import Experiment, ExperimentSession, Participant, SessionStatus, SyntheticVoice
from apps.experiments.tables import ExperimentVersionsTable
from apps.experiments.tasks import async_create_experiment_version
from apps.experiments.views import ExperimentSessionsTableView, ExperimentVersionsTableView
from apps.experiments.views.experiment import (
    base_single_experiment_view,
    start_session_public,
)
from apps.filters.models import FilterSet
from apps.generics import actions
from apps.generics.help import render_help_with_link
from apps.generics.views import paginate_session, render_session_details
from apps.pipelines.views import (
    _pipeline_node_default_values,
    _pipeline_node_parameter_values,
    _pipeline_node_schemas,
    llm_model_parameter_context,
)
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.teams.decorators import login_and_team_required, team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.models import Flag
from apps.trace.models import Trace
from apps.utils.search import similarity_search
from apps.web.waf import WafRule, waf_allow


def _get_alpine_context(request, experiment=None):
    """Add context required by the experiments/settings_content.html template."""
    exclude_services = [SyntheticVoice.OpenAIVoiceEngine]
    if flag_is_active(request, "flag_open_ai_voice_engine"):
        exclude_services = []
    return {
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
    }


@login_and_team_required
@permission_required("experiments.change_experiment", raise_exception=True)
def chatbots_settings(request, team_slug, experiment_id):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)

    team_participant_identifiers = list(
        request.team.participant_set.filter(user=None).values_list("identifier", flat=True)
    )
    team_participant_identifiers.extend(experiment.participant_allowlist)
    team_participant_identifiers = list(set(team_participant_identifiers))
    alpine_context = _get_alpine_context(request, experiment)
    context = {
        "experiment": experiment,
        "request": request,
        **alpine_context,
    }

    if request.method == "POST":
        form = ChatbotSettingsForm(request=request, data=request.POST, instance=experiment)
        if form.is_valid():
            form.save()
            context.update(
                {
                    "edit_mode": False,
                    "form": form,
                    "updated": True,
                }
            )

        else:
            context.update(
                {
                    "edit_mode": True,
                    "form": form,
                    "updated": False,
                    "team_participant_identifiers": team_participant_identifiers,
                }
            )
    else:
        form = ChatbotSettingsForm(request=request, instance=experiment)
        context.update(
            {
                "edit_mode": True,
                "form": form,
                "team_participant_identifiers": team_participant_identifiers,
            }
        )

    return HttpResponse(render_to_string("chatbots/settings_content.html", context, request=request))


@require_GET
@login_and_team_required
@permission_required("experiments.change_experiment", raise_exception=True)
def cancel_edit_mode(request, team_slug, experiment_id):
    experiment = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    context = {
        "experiment": experiment,
        "request": request,
        "edit_mode": False,
    }
    return HttpResponse(render_to_string("chatbots/settings_content.html", context, request=request))


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def chatbots_home(request, team_slug: str):
    actions_ = [
        actions.ModalAction(
            "chatbots:new",
            label="Add New",
            button_style="btn-primary",
            required_permissions=["experiments.add_experiment"],
            modal_template="chatbots/components/new_modal.html",
            modal_context={
                "form": ChatbotForm(request),
                "modal_title": "Create a new Chatbot",
                "form_action": reverse("chatbots:new", args=[team_slug]),
            },
        )
    ]
    return home(request, team_slug, "Chatbots", "chatbots:table", actions=actions_)


class ChatbotExperimentTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    template_name = "table/single_table.html"
    model = Experiment
    table_class = ChatbotTable
    permission_required = "experiments.view_experiment"

    def get_table(self, **kwargs):
        table = super().get_table(**kwargs)
        if not flag_is_active(self.request, "flag_tracing"):
            table.exclude = ("trends",)
        return table

    def get_queryset(self):
        """Returns a lightweight queryset for counting. Expensive annotations are added in get_table_data()."""
        queryset = (
            self.model.objects.get_all()
            .filter(team=self.request.team, working_version__isnull=True, pipeline__isnull=False)
            .select_related("team")
        )
        show_archived = self.request.GET.get("show_archived") == "on"
        if not show_archived:
            queryset = queryset.filter(is_archived=False)

        search = self.request.GET.get("search")
        if search:
            queryset = similarity_search(
                queryset,
                search_phase=search,
                columns=["name", "description"],
                extra_conditions=Q(owner__username__icontains=search),
                score=0.1,
            )

        session_count_subquery = (
            ExperimentSession.objects.filter(experiment_id=OuterRef("pk"))
            .exclude(experiment_channel__platform=ChannelPlatform.EVALUATIONS)
            .values("experiment_id")
            .annotate(count=Count("id"))
            .values("count")
        )

        participant_count_subquery = (
            ExperimentSession.objects.filter(experiment_id=OuterRef("pk"))
            .exclude(experiment_channel__platform=ChannelPlatform.EVALUATIONS)
            .values("experiment_id")
            .annotate(count=Count("participant_id", distinct=True))
            .values("count")
        )

        interaction_count_subquery = (
            Trace.objects.filter(experiment=OuterRef("pk"))
            .exclude(session__experiment_channel__platform=ChannelPlatform.EVALUATIONS)
            .values("experiment_id")
            .annotate(count=Count("id"))
            .values("count")
        )

        last_activity_subquery = (
            ExperimentSession.objects.filter(experiment_id=OuterRef("pk"))
            .exclude(experiment_channel__platform=ChannelPlatform.EVALUATIONS)
            .order_by("-last_activity_at")
            .values("last_activity_at")[:1]
        )

        # Add expensive annotations only to paginated data
        queryset = queryset.annotate(
            session_count=Subquery(session_count_subquery, output_field=IntegerField()),
            participant_count=Subquery(participant_count_subquery, output_field=IntegerField()),
            interaction_count=Subquery(interaction_count_subquery, output_field=IntegerField()),
            last_activity=Subquery(last_activity_subquery, output_field=DateTimeField()),
        ).order_by(F("last_activity").desc(nulls_last=True))
        return queryset


class CreateChatbot(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    model = Experiment
    template_name = "chatbots/chatbot_form.html"
    form_class = ChatbotForm
    title = "Create Chatbot"
    button_title = "Create"
    permission_required = "experiments.add_experiment"

    def get_queryset(self):
        return Experiment.objects.get_all().filter(team=self.request.team)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_tab"] = "chatbots"
        return context

    def form_valid(self, form):
        if form.instance.conversational_consent_enabled and not form.instance.seed_message:
            messages.error(
                request=self.request, message="A seed message is required when conversational consent is enabled!"
            )
            return render(self.request, self.template_name, self.get_context_data())

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
        experiment = get_object_or_404(
            Experiment.objects.get_all().select_related("voice_provider"), id=kwargs["pk"], team=self.request.team
        )
        synthetic_voices = []
        if experiment.voice_provider:
            exclude_services = [SyntheticVoice.OpenAIVoiceEngine]
            if flag_is_active(self.request, "flag_open_ai_voice_engine"):
                exclude_services = []
            synthetic_voices = SyntheticVoice.get_for_team(self.request.team, exclude_services=exclude_services)
            synthetic_voices = synthetic_voices.filter(service__iexact=experiment.voice_provider.type)

        return {
            **data,
            "pipeline_id": experiment.pipeline_id,
            "node_schemas": _pipeline_node_schemas(),
            "experiment": experiment,
            "parameter_values": _pipeline_node_parameter_values(
                team=self.request.team,
                llm_providers=llm_providers,
                llm_provider_models=llm_provider_models,
                synthetic_voices=synthetic_voices,
            ),
            "default_values": _pipeline_node_default_values(llm_providers, llm_provider_models),
            "origin": "chatbots",
            "allow_edit_name": False,
            "flags_enabled": [flag.name for flag in Flag.objects.all() if flag.is_active_for_team(self.request.team)],
            **llm_model_parameter_context(),
        }


@require_POST
@login_and_team_required
@permission_required("experiments.delete_experiment", raise_exception=True)
def archive_chatbot(request, team_slug: str, pk: int):
    chatbot = get_object_or_404(Experiment, id=pk, team=request.team)
    chatbot.archive()
    return HttpResponseClientRedirect(reverse("chatbots:chatbots_home", kwargs={"team_slug": team_slug}))


class CreateChatbotVersion(LoginAndTeamRequiredMixin, FormView, PermissionRequiredMixin):
    model = Experiment
    form_class = ExperimentVersionForm
    template_name = "experiments/create_version_form.html"
    title = "Create Chatbot Version"
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
            raise PermissionDenied("Unable to version an archived chatbot.")

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
        from apps.assistants.sync import OpenAiSyncError

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
        from apps.assistants.sync import get_diff_with_openai_assistant, get_out_of_sync_files

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
            "chatbots:single_chatbot_home",
            kwargs={
                "team_slug": self.request.team.slug,
                "experiment_id": self.kwargs["experiment_id"],
            },
        )
        return f"{url}#versions"


class ChatbotVersionsTableView(ExperimentVersionsTableView):
    model = Experiment
    table_class = ExperimentVersionsTable
    template_name = "experiments/experiment_version_table.html"
    permission_required = "experiments.view_experiment"


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def chatbot_version_details(request, team_slug: str, experiment_id: int, version_number: int):
    try:
        experiment_version = Experiment.objects.get_all().get(
            team=request.team, working_version_id=experiment_id, version_number=version_number
        )
    except Experiment.DoesNotExist:
        raise Http404() from None

    context = {"version_details": experiment_version.version_details, "experiment": experiment_version}
    return render(request, "experiments/components/experiment_version_details_content.html", context)


@login_and_team_required
@permission_required("experiments.view_experiment", raise_exception=True)
def chatbot_version_create_status(
    request,
    team_slug: str,
    experiment_id: int,
):
    experiment = Experiment.objects.get(id=experiment_id, team=request.team)
    return TemplateResponse(
        request,
        "experiments/create_version_button.html",
        {
            "active_tab": "chatbots",
            "experiment": experiment,
            "trigger_refresh": experiment.create_version_task_id is not None,
        },
    )


class ChatbotSessionsTableView(ExperimentSessionsTableView):
    table_class = ChatbotSessionsTable

    def get_table_data(self):
        """Add message_count annotation to the paginated data."""
        queryset = super().get_table_data()
        return queryset.annotate_with_message_count()

    def get_table(self, **kwargs):
        """
        When viewing sessions for a specific chatbot, hide the chatbot column
        """
        table = super().get_table(**kwargs)
        if self.kwargs.get("experiment_id"):
            table.exclude = ("chatbot",)
        return table


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


@require_POST
@login_and_team_required
@permission_required("experiments.change_experimentsession", raise_exception=True)
def end_chatbot_session(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    experiment_session = get_object_or_404(
        ExperimentSession,
        experiment__public_id=experiment_id,
        external_id=session_id,
        team=request.team,
    )
    propagate_event = request.POST.get("fire_end_event") == "on"
    experiment_session.end(propagate=propagate_event)
    messages.success(request, "Session ended")
    return redirect("chatbots:chatbot_session_view", team_slug, experiment_id, session_id)


@login_and_team_required
def chatbot_chat_session(request, team_slug: str, experiment_id: int, version_number: int, session_id: int):
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
        "experiments/chat/web_chat.html",
        {"experiment": experiment, "session": session, "active_tab": "chatbots", **version_specific_vars},
    )


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
@permission_required("experiments.invite_participants", raise_exception=True)
def chatbot_invitations(request, team_slug: str, experiment_id: int):
    chatbot = get_object_or_404(Experiment, id=experiment_id, team=request.team)
    chatbot_version = chatbot.default_version
    sessions = chatbot.sessions.order_by("-created_at").filter(
        status__in=["setup", "pending"],
        participant__isnull=False,
    )
    from apps.experiments.forms import ExperimentInvitationForm

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
                from django.db import transaction

                with transaction.atomic():
                    session = WebChannel.start_new_session(
                        chatbot,
                        participant_identifier=post_form.cleaned_data["email"],
                        session_status=SessionStatus.SETUP,
                        timezone=request.session.get("detected_tz", None),
                    )
                if post_form.cleaned_data["invite_now"]:
                    from apps.experiments.email import send_experiment_invitation

                    send_experiment_invitation(session)
        else:
            form = post_form

    version_specific_vars = {
        "chatbot_name": chatbot_version.name,
        "chatbot_description": chatbot_version.description,
    }
    return TemplateResponse(
        request,
        "chatbots/chatbot_invitations.html",
        {"invitation_form": form, "experiment": chatbot, "sessions": sessions, **version_specific_vars},
    )


@waf_allow(WafRule.NoUserAgent_HEADER)
@team_required
def start_chatbot_session_public(request, team_slug: str, experiment_id: uuid.UUID):
    return start_session_public(request, team_slug, experiment_id)


@waf_allow(WafRule.NoUserAgent_HEADER)
@experiment_session_view(allowed_states=[SessionStatus.ACTIVE, SessionStatus.SETUP])
@verify_session_access_cookie
def chatbot_chat(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    return _chatbot_chat_ui(request)


@xframe_options_exempt
@team_required
def start_chatbot_session_public_embed(request, team_slug: str, experiment_id: uuid.UUID):
    """Special view for starting chatbot sessions from embedded widgets. This will ignore consent and pre-surveys and
    will ALWAYS create anonymous participants."""
    try:
        chatbot = get_object_or_404(Experiment, public_id=experiment_id, team=request.team)
    except ValidationError:
        # old links dont have uuids
        raise Http404() from None

    chatbot_version = chatbot.default_version
    if not chatbot_version.is_public:
        raise Http404

    participant = Participant.create_anonymous(request.team, ChannelPlatform.WEB)
    session = WebChannel.start_new_session(
        working_experiment=chatbot,
        participant_identifier=participant.identifier,
        timezone=request.session.get("detected_tz", None),
        metadata={Chat.MetadataKeys.EMBED_SOURCE: request.headers.get("referer", None)},
    )
    return redirect("chatbots:chatbot_chat_embed", team_slug, chatbot.public_id, session.external_id)


@experiment_session_view(allowed_states=[SessionStatus.ACTIVE, SessionStatus.SETUP])
@xframe_options_exempt
def chatbot_chat_embed(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    """Special view for embedding that doesn't have the cookie security. This is OK because of the additional
    checks to ensure the participant is 'anonymous'."""
    session = request.experiment_session
    if not session.participant.is_anonymous:
        raise Http404
    return _chatbot_chat_ui(request, embedded=True)


def _chatbot_chat_ui(request, embedded=False):
    chatbot_version = request.experiment.default_version
    version_specific_vars = {
        "assistant": chatbot_version.get_assistant(),
        "chatbot_name": chatbot_version.name,
        "experiment_version": chatbot_version,
        "experiment_version_number": chatbot_version.version_number,
    }
    return TemplateResponse(
        request,
        "experiments/chat/web_chat.html",
        {
            "experiment": request.experiment,
            "session": request.experiment_session,
            "active_tab": "chatbots",
            "embedded": embedded,
            **version_specific_vars,
        },
    )


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
        return redirect("chatbots:single_chatbot_home", team_slug=team_slug, experiment_id=new_experiment.id)
    else:
        experiment_id = kwargs["pk"]
        return single_chatbot_home(request, team_slug, experiment_id)


def home(
    request,
    team_slug: str,
    title: str,
    table_url_name: str,
    actions=None,
    show_modal_or_banner=False,
):
    """
    Renders the home page for chatbots with the given parameters.

    Arguments:
        request: The current request.
        team_slug: The slug of the team.
        title: The title of the page.
        table_url_name: The url name of the table.
        actions: List of `apps.generics.actions.Action` objects to display in the title.
        show_modal_or_banner: Temporary flag for experiment deprecation notice.
    """
    help_text_keys = {
        "Experiments": "experiment",
        "Chatbots": "chatbots",
    }
    help_key = help_text_keys.get(title, title.lower())  # Default to lowercase if missing
    return TemplateResponse(
        request,
        "chatbots/home.html",
        {
            "active_tab": title.lower(),
            "title": title,
            "title_help_content": render_help_with_link("", help_key),
            "table_url": reverse(table_url_name, args=[team_slug]),
            "enable_search": True,
            "toggle_archived": True,
            "show_modal_or_banner": show_modal_or_banner,
            "actions": actions,
        },
    )


class AllSessionsHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    template_name = "generic/object_home.html"
    permission_required = "experiments.view_experimentsession"

    def get_context_data(self, team_slug: str, **kwargs):
        table_url = reverse("chatbots:all_sessions_list", kwargs={"team_slug": team_slug})
        filter_context = get_filter_context_data(
            team=self.request.team,
            columns=ExperimentSessionFilter.columns(self.request.team),
            date_range_column="last_message",
            table_url=table_url,
            table_container_id="data-table",
            table_type=FilterSet.TableType.ALL_SESSIONS,
        )

        return {
            "active_tab": "all_sessions",
            "title": "All Sessions",
            "allow_new": False,
            "table_url": table_url,
            "use_dynamic_filters": True,
            **filter_context,
        }
