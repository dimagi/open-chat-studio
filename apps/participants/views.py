import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, TemplateView
from django_tables2 import RequestConfig, SingleTableView

from apps.annotations.prefetch import chat_tagged_items_prefetch
from apps.api.tasks import trigger_bot_message_task
from apps.api.trigger_bot import TriggerBotMessageError, prepare_trigger_bot_message
from apps.channels.models import ChannelPlatform
from apps.chatbots.tables import ParticipantSessionsTable
from apps.cost_tracking.services.reporting import CostFilters, costs_by_participant
from apps.experiments.models import Experiment, ExperimentSession, Participant, ParticipantData
from apps.filters.models import FilterSet
from apps.participants.forms import ParticipantExportForm, ParticipantForm, ParticipantImportForm, TriggerBotForm
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin

from ..events.models import ScheduledMessage
from ..experiments.filters import ExperimentSessionFilter, get_filter_context_data
from ..generics import actions
from ..web.dynamic_filters.datastructures import FilterParams
from .filters import ParticipantFilter
from .import_export import export_participant_data_to_response, process_participant_import
from .tables import ParticipantTable

IMPORT_PERMISSIONS = [
    "experiments.add_participant",
    "experiments.change_participant",
    "experiments.add_participantdata",
    "experiments.change_participantdata",
]

# Same window as the dashboard's default date range (apps/dashboard/forms.py).
PARTICIPANT_COST_WINDOW_DAYS = 30


def _parse_chatbot_filter(request) -> int | None:
    """Parse the `?chatbot=` query param, treating anything unparsable as "All chatbots"."""
    raw = request.GET.get("chatbot")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _apply_session_filters(sessions, request):
    """Apply the Sessions tab's Search transcripts / State / Tag filters to a session queryset."""
    search_query = request.GET.get("q", "").strip()
    if search_query:
        sessions = sessions.filter(chat__messages__content__icontains=search_query).distinct()
    state_filter = request.GET.get("state", "").strip()
    if state_filter == "active":
        sessions = sessions.filter(ended_at__isnull=True)
    elif state_filter == "ended":
        sessions = sessions.filter(ended_at__isnull=False)
    tag_filter = request.GET.get("tag", "").strip()
    if tag_filter:
        sessions = sessions.filter(chat__tags__name__icontains=tag_filter).distinct()
    return sessions


def _resolve_selected_data_experiment(
    experiments: list[Experiment], filter_experiment_id: int | None, experiment_id: int | None
) -> Experiment | None:
    """Pick which chatbot the Participant Data tab shows: the active pill, an explicit
    experiment, or the first chatbot the participant has used, in that order."""
    selected_id = filter_experiment_id or experiment_id or (experiments[0].id if experiments else None)
    if not selected_id:
        return None
    return next((e for e in experiments if e.id == selected_id), None)


def single_participant_home_context(
    request, context: dict, participant_id: int, experiment_id: int | None = None
) -> dict:
    """A helper function to build context for a single participant's home view.

    Loads sessions and schedules across every chatbot the participant has used (not
    gated behind picking one first). A shared `?chatbot=` query param, applied via plain
    links (full page reload, no new client-side filtering logic), narrows the Sessions
    and Schedules tabs to one chatbot -- "All chatbots" simply omits the param. The
    Participant Data tab always shows one chatbot at a time (there's no sensible
    "all chatbots" merged JSON view), selected the same way, defaulting to the first
    chatbot the participant has used.
    """
    team = request.team
    participant = get_object_or_404(Participant, pk=participant_id, team=team)
    experiments = list(participant.get_experiments_for_display())

    filter_experiment_id = _parse_chatbot_filter(request)

    total_session_count = ExperimentSession.objects.filter(team=team, participant=participant).count()
    sessions = (
        ExperimentSession.objects.get_table_queryset(team, filter_experiment_id)
        .filter(participant=participant)
        .prefetch_related(chat_tagged_items_prefetch())
    )
    sessions = _apply_session_filters(sessions, request)
    table = ParticipantSessionsTable(sessions)
    # set request (no pagination) so the chatbot chip can permission-gate its link
    session_table = RequestConfig(request, paginate=False).configure(table)

    schedules = participant.get_schedules_for_all_experiments(include_inactive=True)
    if filter_experiment_id:
        schedules = [s for s in schedules if s["experiment"].id == filter_experiment_id]

    selected_data_experiment = _resolve_selected_data_experiment(experiments, filter_experiment_id, experiment_id)
    participant_data_row = None
    if selected_data_experiment:
        participant_data_row = (
            ParticipantData.objects.for_experiment(selected_data_experiment).filter(participant=participant).first()
        )

    filter_context = get_filter_context_data(
        team=team,
        columns=ExperimentSessionFilter.columns(team),
        filter_class=ExperimentSessionFilter,
        table_url=reverse("chatbots:participant_sessions_list", args=[team.slug, participant_id]),
        table_container_id="participant-sessions-table",
        table_type=FilterSet.TableType.ALL_SESSIONS,
    )

    context.update(
        {
            "active_tab": "participants",
            "participant": participant,
            "experiments": experiments,
            "selected_experiment_id": filter_experiment_id,
            "selected_data_experiment": selected_data_experiment,
            "session_table": session_table,
            "total_session_count": total_session_count,
            "sessions_panel_url": reverse("participants:sessions-panel", args=[team.slug, participant_id]),
            "data_panel_url": reverse("participants:data-panel", args=[team.slug, participant_id]),
            **filter_context,
            "participant_data": json.dumps(participant_data_row.data if participant_data_row else {}, indent=4),
            "updated_at": participant_data_row.updated_at if participant_data_row else None,
            "key_count": len(participant_data_row.data) if participant_data_row else 0,
            "participant_schedules": schedules,
            "message_trend": participant.get_message_trend(),
            "latest_session": participant.experimentsession_set.order_by("-created_at").first(),
        }
    )
    if participant.platform not in ChannelPlatform.team_global_platforms():
        context["trigger_bot_form"] = TriggerBotForm(participant=participant)
    return context


class ParticipantHome(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"
    permission_required = "experiments.view_participant"

    def get_context_data(self, team_slug: str, **kwargs):  # ty: ignore[invalid-method-override]
        table_url = reverse("participants:participant_table", kwargs={"team_slug": team_slug})
        filter_context = get_filter_context_data(
            self.request.team,
            columns=ParticipantFilter.columns(self.request.team),
            filter_class=ParticipantFilter,
            table_url=table_url,
            table_container_id="data-table",
            table_type=FilterSet.TableType.PARTICIPANTS,
        )

        return {
            "active_tab": "participants",
            "title": "Participants",
            "allow_new": False,
            "table_url": table_url,
            "actions": [
                actions.Action(
                    "participants:participant_new",
                    label="Add new",
                    title="Create participant",
                    button_style="btn-primary",
                    required_permissions=["experiments.add_participant"],
                ),
                actions.Action(
                    "participants:import",
                    label="Import",
                    icon_class="fa-solid fa-file-import",
                    button_style="btn-primary",
                    title="Import participants",
                    required_permissions=IMPORT_PERMISSIONS,
                ),
                actions.ModalAction(
                    "participants:export",
                    label="Export",
                    icon_class="fa-solid fa-download",
                    button_style="btn-primary",
                    required_permissions=["experiments.view_participant", "experiments.view_participantdata"],
                    modal_template="participants/components/export_modal.html",
                    modal_context={
                        "form": ParticipantExportForm(team=self.request.team),
                        "modal_title": "Export Participant Data",
                    },
                ),
            ],
            **filter_context,
        }


class CreateParticipant(LoginAndTeamRequiredMixin, PermissionRequiredMixin, CreateView):
    permission_required = "experiments.add_participant"
    model = Participant
    form_class = ParticipantForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Participant",
        "button_text": "Create",
        "active_tab": "participants",
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["team"] = self.request.team
        return kwargs

    def form_valid(self, form):
        form.instance.team = self.request.team
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "participants:single-participant-home",
            kwargs={"team_slug": self.request.team.slug, "participant_id": self.object.id},
        )


class ParticipantTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):  # ty: ignore[invalid-method-override]
    model = Participant
    table_class = ParticipantTable
    template_name = "table/single_table.html"
    permission_required = "experiments.view_participant"

    def get_queryset(self):
        query = Participant.objects.filter(team=self.request.team)
        timezone = self.request.session.get("detected_tz", None)
        filter_set = ParticipantFilter()
        query = filter_set.apply(query, filter_params=FilterParams.from_request(self.request), timezone=timezone)
        return query

    def get_table(self, **kwargs):
        """Attach per-page cost to the table.

        Hooking after `RequestConfig.configure` means the queryset is already
        paginated, so the cost read is bounded to one page of participants -
        `UsageRecord` has no `(team, participant)` index, which is also why the
        column is not sortable.
        """
        table = super().get_table(**kwargs)
        page = getattr(table, "page", None)
        # `page.object_list` holds django-tables2 `BoundRow` wrappers, not the underlying
        # `Participant` instances - unwrap via `.record`, the same pattern
        # `attach_chat_tagged_items` uses for the same paginator shape.
        page_ids = [getattr(row, "record", row).id for row in page.object_list] if page is not None else []
        end = timezone.now()
        start = end - timedelta(days=PARTICIPANT_COST_WINDOW_DAYS)
        table.cost_map = (
            costs_by_participant(
                self.request.team,
                start=start,
                end=end,
                filters=CostFilters(participant_ids=page_ids),
            )
            if page_ids
            else {}
        )
        return table


class SingleParticipantHome(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "experiments.view_participant"
    template_name = "participants/single_participant_home.html"

    def get_context_data(self, *args, **kwargs):
        initial_context = super().get_context_data(*args, **kwargs)
        participant_id = self.kwargs["participant_id"]
        experiment_id = self.kwargs.get("experiment_id")
        return single_participant_home_context(
            self.request, initial_context, participant_id=participant_id, experiment_id=experiment_id
        )


class ParticipantSessionsPanel(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Sessions tab content, re-rendered via htmx when a chatbot quick-filter pill is clicked.

    Reuses the same context builder as the full page so a pill click and a full page load
    produce identical data. Renders a dedicated fragment rather than the full-page partial:
    that one also includes `experiments/filters.html`, whose Alpine component re-mounts (and
    re-fires its own auto-load) on every swap if included here too, racing this endpoint's
    own htmx-swapped table content -- no full page reload, so no scroll jump or reload flash.
    """

    permission_required = "experiments.view_participant"
    template_name = "participants/partials/participant_sessions_panel.html"

    def get_context_data(self, *args, **kwargs):
        initial_context = super().get_context_data(*args, **kwargs)
        return single_participant_home_context(
            self.request, initial_context, participant_id=self.kwargs["participant_id"]
        )


class ParticipantSchedulesPanel(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Schedules tab content, re-rendered via htmx when a chatbot quick-filter pill is clicked."""

    permission_required = "experiments.view_participant"
    template_name = "participants/partials/participant_schedules_table.html"

    def get_context_data(self, *args, **kwargs):
        initial_context = super().get_context_data(*args, **kwargs)
        return single_participant_home_context(
            self.request, initial_context, participant_id=self.kwargs["participant_id"]
        )


class ParticipantDataPanel(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Participant Data tab content, re-rendered via htmx when a chatbot pill is clicked."""

    permission_required = "experiments.view_participant"
    template_name = "participants/partials/participant_data_panel.html"

    def get_context_data(self, *args, **kwargs):
        initial_context = super().get_context_data(*args, **kwargs)
        return single_participant_home_context(
            self.request, initial_context, participant_id=self.kwargs["participant_id"]
        )


class EditParticipantData(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "experiments.change_participantdata"

    def post(self, request, team_slug, participant_id, experiment_id):
        experiment = get_object_or_404(Experiment, team=request.team, id=experiment_id)
        participant = get_object_or_404(Participant, team=request.team, id=participant_id)
        error = ""
        new_data = None
        raw_data = request.POST.get("participant-data", "")
        try:
            new_data = json.loads(raw_data)
        except json.JSONDecodeError:
            error = "Data must be a valid JSON object"
        else:
            if not isinstance(new_data, dict):
                error = "Data must be a valid JSON object"

        updated_at = None
        key_count = 0
        if not error:
            # ParticipantData is always keyed to the working version's id, so a chatbot
            # running as a published version needs resolving back to it here too.
            working_experiment_id = experiment.working_version_id if experiment.is_a_version else experiment.id
            data_row, _ = ParticipantData.objects.update_or_create(
                participant=participant,
                experiment_id=working_experiment_id,
                team=request.team,
                defaults={"team": experiment.team, "data": new_data},
            )
            updated_at = data_row.updated_at
            key_count = len(new_data)
        return render(
            request,
            "participants/partials/participant_data.html",
            {
                "experiment": experiment,
                "participant": participant,
                "participant_data": json.dumps(new_data, indent=4) if not error else raw_data,
                "error": error,
                "just_saved": not error,
                "updated_at": updated_at,
                "key_count": key_count,
            },
        )


@login_and_team_required
@permission_required("experiments.change_participant")
def edit_name(request, team_slug: str, pk: int):
    participant = get_object_or_404(Participant, id=pk, team=request.team)
    if request.method == "POST":
        if name := request.POST.get("name"):
            participant.name = name
            participant.save()
        return render(request, "participants/partials/participant_name.html", {"participant": participant})
    return render(request, "participants/partials/edit_name.html", {"participant": participant})


@login_and_team_required
@permission_required("experiments.change_participant")
@require_POST
def cancel_schedule(request, team_slug: str, participant_id: int, schedule_id: str):
    schedule = get_object_or_404(
        ScheduledMessage, external_id=schedule_id, participant_id=participant_id, team=request.team
    )
    experiment = schedule.experiment
    schedule.cancel(cancelled_by=request.user)
    schedule_dict = schedule.as_dict()
    schedule_dict["experiment"] = experiment
    return render(
        request,
        "participants/partials/participant_schedule_row.html",
        {"schedule": schedule_dict, "participant_id": participant_id},
    )


@permission_required("experiments.view_participant")
@login_and_team_required
def participant_identifiers_by_experiment(request, team_slug: str, experiment_id: int):
    query = (
        Participant.objects.filter(team=request.team, experimentsession__experiment_id=experiment_id)
        .values_list("identifier", "remote_id")
        .distinct()
    )
    return _get_identifiers_response(query)


@permission_required("experiments.view_participant")
@login_and_team_required
def all_participant_identifiers(request, team_slug: str):
    query = Participant.objects.filter(team=request.team).values_list("identifier", "remote_id").distinct()
    return _get_identifiers_response(query)


def _get_identifiers_response(queryset):
    identifiers, remote_ids = set(), set()
    for ident, remote_id in queryset:
        if ident:
            identifiers.add(ident)
        if remote_id:
            remote_ids.add(remote_id)
    return JsonResponse(
        {
            "identifiers": list(identifiers),
            "remote_ids": list(remote_ids),
        },
        safe=False,
    )


@permission_required(IMPORT_PERMISSIONS)
@login_and_team_required
def import_participants(request, team_slug: str):
    form = ParticipantImportForm(team=request.team)
    import_results = None

    if request.method == "POST":
        form = ParticipantImportForm(request.POST, request.FILES, team=request.team)
        if form.is_valid():
            try:
                import_results = process_participant_import(
                    form.cleaned_data["file"], form.cleaned_data["experiment"], request.team
                )

                # Only redirect if there are no errors
                if not import_results["errors"]:
                    success_msg = (
                        f"Successfully imported {import_results['created']} participants, "
                        f"updated {import_results['updated']} participants"
                    )
                    messages.success(request, success_msg)
                    return redirect("participants:participant_home", team_slug=team_slug)
            except Exception as e:
                messages.error(request, f"Import failed: {str(e)}")

    return render(request, "participants/participant_import.html", {"form": form, "import_results": import_results})


@permission_required(["experiments.view_participant", "experiments.view_participantdata"])
@login_and_team_required
def export_participants(request, team_slug: str):
    form = ParticipantExportForm(request.POST, team=request.team)

    if not form.is_valid():
        return HttpResponse("Invalid form data", status=400)

    experiment = form.cleaned_data.get("experiment")

    query = Participant.objects.filter(team=request.team)
    if experiment:
        query = query.filter(data_set__experiment=experiment).distinct()

    filter_set = ParticipantFilter()
    timezone = request.session.get("detected_tz", None)
    query = filter_set.apply(
        query, filter_params=FilterParams.from_request_header(request, "referer"), timezone=timezone
    )

    return export_participant_data_to_response(request.team, experiment, query)


class DeleteParticipant(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "experiments.delete_participant"

    def delete(self, request, team_slug: str, pk: int):
        participant = get_object_or_404(Participant, id=pk, team=request.team)
        participant.delete()
        messages.success(request, "Participant deleted")
        return HttpResponse()


@login_and_team_required
@permission_required(["experiments.change_participant"])
@require_POST
def trigger_bot(request, team_slug: str, participant_id: int):
    """
    Trigger a bot to send a message to a participant
    """
    participant = get_object_or_404(Participant, id=participant_id, team=request.team)
    form = TriggerBotForm(request.POST, participant=participant)

    if not form.is_valid():
        messages.error(request, "Please check the form for errors")
        return _render_trigger_bot_form(request, participant_id, form)

    try:
        # Shared with the API's trigger-bot endpoint: the session has to exist before the task runs,
        # and both callers must agree on the task's arguments (#4221).
        session, _ = prepare_trigger_bot_message(
            form.cleaned_data["experiment"],
            participant.identifier,
            participant.platform,
            start_new_session=form.cleaned_data["start_new_session"],
            session_data=form.cleaned_data.get("session_data"),
        )
    except TriggerBotMessageError as error:
        form.add_error(None, error.detail)
        return _render_trigger_bot_form(request, participant_id, form)

    # No message_text: this form only sends prompts through the bot, never verbatim messages.
    trigger_bot_message_task.delay_on_commit(str(session.external_id), form.cleaned_data["prompt_text"], None)

    messages.success(request, f"Bot message triggered for {participant}")
    return redirect("participants:single-participant-home", team_slug=team_slug, participant_id=participant_id)


def _render_trigger_bot_form(request, participant_id: int, form: TriggerBotForm):
    """Re-render the participant page with the errored form (the template reopens the dialog)."""
    context = single_participant_home_context(request, {}, participant_id=participant_id)
    context["trigger_bot_form"] = form
    return render(request, "participants/single_participant_home.html", context=context)
