import json

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, TemplateView
from django_tables2 import SingleTableView

from apps.experiments.models import Experiment, ExperimentSession, Participant, ParticipantData
from apps.participants.forms import ParticipantExportForm, ParticipantForm, ParticipantImportForm
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin

from ..events.models import ScheduledMessage
from ..experiments.filters import get_filter_context_data
from ..experiments.tables import ExperimentSessionsTable
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


class ParticipantHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    template_name = "generic/object_home.html"
    permission_required = "experiments.view_participant"

    def get_context_data(self, team_slug: str, **kwargs):
        table_url = reverse("participants:participant_table", kwargs={"team_slug": team_slug})
        filter_context = get_filter_context_data(
            self.request.team, ParticipantFilter.columns(self.request.team), "created_on", table_url, "data-table"
        )

        return {
            "active_tab": "participants",
            "title": "Participants",
            "allow_new": False,
            "table_url": table_url,
            "actions": [
                actions.Action(
                    "participants:import",
                    label="Import",
                    icon_class="fa-solid fa-file-import",
                    title="Import participants",
                    required_permissions=IMPORT_PERMISSIONS,
                ),
                actions.ModalAction(
                    "participants:export",
                    label="Export",
                    icon_class="fa-solid fa-download",
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


class CreateParticipant(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    permission_required = "experiments.add_participant"
    model = Participant
    form_class = ParticipantForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Participant",
        "button_text": "Create",
        "active_tab": "participants",
    }

    def get_success_url(self):
        return reverse("participants:participant_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class ParticipantTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    model = Participant
    paginate_by = 25
    table_class = ParticipantTable
    template_name = "table/single_table.html"
    permission_required = "experiments.view_participant"

    def get_queryset(self):
        query = Participant.objects.filter(team=self.request.team)
        timezone = self.request.session.get("detected_tz", None)
        filter_set = ParticipantFilter()
        query = filter_set.apply(query, filter_params=FilterParams.from_request(self.request), timezone=timezone)
        return query


class SingleParticipantHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "experiments.view_participant"
    template_name = "participants/single_participant_home.html"

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        participant = get_object_or_404(Participant, pk=self.kwargs["participant_id"])
        context["active_tab"] = "participants"
        context["participant"] = participant
        participant_experiments = participant.get_experiments_for_display()

        if experiment_id := self.kwargs.get("experiment_id"):
            experiment = participant_experiments.get(id=experiment_id)
        else:
            experiment = participant_experiments.first()

        context["experiments"] = participant_experiments
        context["selected_experiment"] = experiment
        sessions = participant.experimentsession_set.filter(experiment=experiment).all()
        context["session_table"] = ExperimentSessionsTable(
            ExperimentSession.objects.annotate_with_last_message_created_at(sessions),
            extra_columns=[("participant", None)],  # remove participant column
        )
        data = participant.get_data_for_experiment(experiment)
        context["participant_data"] = json.dumps(data, indent=4)
        context["participant_schedules"] = participant.get_schedules_for_experiment(
            experiment, as_dict=True, include_inactive=True
        )
        return context


class EditParticipantData(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "experiments.change_participantdata"

    def post(self, request, team_slug, participant_id, experiment_id):
        experiment = get_object_or_404(Experiment, team__slug=team_slug, id=experiment_id)
        participant = get_object_or_404(Participant, team__slug=team_slug, id=participant_id)
        error = ""
        raw_data = request.POST["participant-data"]
        try:
            new_data = json.loads(raw_data)
        except json.JSONDecodeError:
            error = "Data must be a valid JSON object"
        else:
            if not isinstance(new_data, dict):
                error = "Data must be a valid JSON object"

        if not error:
            ParticipantData.objects.update_or_create(
                participant=participant,
                experiment_id=experiment_id,
                team=request.team,
                defaults={"team": experiment.team, "data": new_data},
            )
        return render(
            request,
            "participants/partials/participant_data.html",
            {
                "experiment": experiment,
                "participant": participant,
                "participant_data": json.dumps(new_data, indent=4) if not error else raw_data,
                "error": error,
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
    ).prefetch_related("attempts")
    schedule.cancel(cancelled_by=request.user)
    return render(
        request,
        "participants/partials/participant_schedule_single.html",
        {"schedule": schedule.as_dict(), "participant_id": participant_id},
    )


@permission_required("experiments.view_participant")
@login_and_team_required
def participant_identifiers_by_experiment(request, team_slug: str, experiment_id: int):
    query = (
        Participant.objects.filter(team__slug=team_slug, experimentsession__experiment_id=experiment_id)
        .values_list("identifier", "remote_id")
        .distinct()
    )
    return _get_identifiers_response(query)


@permission_required("experiments.view_participant")
@login_and_team_required
def all_participant_identifiers(request, team_slug: str):
    query = Participant.objects.filter(team__slug=team_slug).values_list("identifier", "remote_id").distinct()
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
@permission_required("experiments.change_participant")
def edit_identifier(request, team_slug: str, pk: int):
    participant = get_object_or_404(Participant, id=pk, team=request.team)

    if request.method == "POST":
        new_identifier = request.POST.get("identifier", "").strip()

        if not new_identifier:
            return render(
                request,
                "participants/partials/edit_identifier.html",
                {"participant": participant, "error": "Identifier is required"},
            )

        # Check if the new identifier is the same as the current one
        if new_identifier == participant.identifier:
            return render(request, "participants/partials/participant_identifier.html", {"participant": participant})

        # Check if another participant with this identifier already exists
        try:
            existing_participant = Participant.objects.get(
                team=request.team, platform=participant.platform, identifier=new_identifier
            )
            # Merge participants
            _merge_participants(participant, existing_participant)
            messages.success(request, f"Participant merged with existing participant '{new_identifier}' and removed")
            # Return a response that triggers a redirect to the participant list
            response = HttpResponse()
            response["HX-Redirect"] = reverse("participants:participant_home", args=[team_slug])
            return response

        except Participant.DoesNotExist:
            # No conflict, just update the identifier
            participant.identifier = new_identifier
            participant.save()
            return render(request, "participants/partials/participant_identifier.html", {"participant": participant})

    return render(request, "participants/partials/edit_identifier.html", {"participant": participant})


def _merge_participants(old_participant: Participant, new_participant: Participant):
    """
    Merge old_participant into new_participant and delete old_participant.

    This will:
    1. Merge ParticipantData for each experiment
    2. Transfer all sessions to the new participant
    3. Transfer all scheduled messages to the new participant
    4. Delete the old participant
    """
    with transaction.atomic():
        # 1. Merge ParticipantData for each experiment
        old_data_records = ParticipantData.objects.filter(participant=old_participant).select_for_update()

        for old_data in old_data_records:
            try:
                # Check if new participant already has data for this experiment
                new_data = ParticipantData.objects.get(participant=new_participant, experiment=old_data.experiment)
                # Merge the data dictionaries (new_participant's data takes precedence)
                merged_data = old_data.data | new_data.data
                new_data.data = merged_data
                new_data.save()
                # Delete the old data record
                old_data.delete()
            except ParticipantData.DoesNotExist:
                # New participant doesn't have data for this experiment, transfer it
                old_data.participant = new_participant
                old_data.save()

        # 2. Transfer all sessions to the new participant
        ExperimentSession.objects.filter(participant=old_participant).update(participant=new_participant)

        # 3. Transfer all scheduled messages to the new participant
        ScheduledMessage.objects.filter(participant=old_participant).update(participant=new_participant)

        # 4. Delete the old participant
        old_participant.delete()
