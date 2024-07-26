import json

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import CreateView, TemplateView
from django_tables2 import SingleTableView

from apps.experiments.models import Experiment, Participant, ParticipantData
from apps.participants.forms import ParticipantForm
from apps.teams.mixins import LoginAndTeamRequiredMixin

from .tables import ParticipantTable


class ParticipantHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    template_name = "generic/object_home.html"
    permission_required = "experiments.view_participant"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "participants",
            "title": "Participants",
            "allow_new": False,
            "table_url": reverse("participants:participant_table", args=[team_slug]),
            "enable_search": True,
        }


class CreateParticipant(CreateView, PermissionRequiredMixin):
    permission_required = "experiments.add_participant"
    model = Participant
    form_class = ParticipantForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Tag",
        "button_text": "Create",
        "active_tab": "tags",
    }

    def get_success_url(self):
        return reverse("participants:participant_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class ParticipantTableView(SingleTableView):
    model = Participant
    paginate_by = 25
    table_class = ParticipantTable
    template_name = "table/single_table.html"
    permission_required = "experiments.view_participant"

    def get_queryset(self):
        query = Participant.objects.filter(team=self.request.team)
        search = self.request.GET.get("search")
        if search:
            query = query.filter(Q(identifier__icontains=search) | Q(platform__iexact=search))
        return query


class SingleParticipantHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "experiments.view_participant"
    template_name = "participants/single_participant_home.html"

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        participant = Participant.objects.prefetch_related("experimentsession_set").get(
            id=self.kwargs["participant_id"]
        )
        context["active_tab"] = "participants"
        context["participant"] = participant
        experiment_data = {}
        for experiment in participant.get_experiments_for_display():
            sessions = participant.experimentsession_set.filter(experiment=experiment).all()
            experiment_data[experiment] = {
                "sessions": sessions,
                "participant_data": json.dumps(participant.get_data_for_experiment(experiment), indent=4),
            }
        context["experiment_data"] = experiment_data
        return context


class EditParticipantData(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "experiments.change_participantdata"

    def post(self, request, team_slug, participant_id, experiment_id):
        experiment = get_object_or_404(Experiment, team__slug=team_slug, id=experiment_id)
        participant = get_object_or_404(Participant, team__slug=team_slug, id=participant_id)
        new_data = json.loads(request.POST["participant-data"])
        ParticipantData.objects.update_or_create(
            participant=participant,
            content_type__model="experiment",
            object_id=experiment_id,
            team=request.team,
            defaults={"team": experiment.team, "data": new_data, "content_object": experiment},
        )
        return redirect(reverse("participants:single-participant-home", args=[self.request.team.slug, participant_id]))
