from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.generics import ListAPIView

from apps.api.permissions import HasUserAPIKey
from apps.experiments.models import Experiment, Participant, ParticipantData

require_view_experiment = permission_required("experiments.view_experiment")


class ExperimentSerializer(serializers.Serializer):
    name = serializers.CharField()
    experiment_id = serializers.UUIDField(source="public_id")


@method_decorator(require_view_experiment, name="get")
class ExperimentsView(ListAPIView):
    permission_classes = [HasUserAPIKey]
    serializer_class = ExperimentSerializer

    def get_queryset(self):
        return Experiment.objects.filter(team__slug=self.request.team.slug).all()


@api_view(["POST"])
@permission_classes([HasUserAPIKey])
@permission_required("experiments.change_participantdata")
def update_participant_data(request, participant_id: str):
    """
    Upsert participant data for all specified experiments in the payload
    """
    experiment_data = request.data
    experiment_ids = experiment_data.keys()
    experiments = Experiment.objects.filter(public_id__in=experiment_ids, team=request.team)
    experiment_map = {str(experiment.public_id): experiment for experiment in experiments}
    participant = get_object_or_404(Participant, identifier=participant_id, team=request.team)

    missing_ids = set(experiment_ids) - set(experiment_map)
    if missing_ids:
        response = {"errors": [{"message": f"Experiment {experiment_id} not found"} for experiment_id in missing_ids]}
        return JsonResponse(data=response, status=404)

    for experiment_id, new_data in experiment_data.items():
        experiment = experiment_map[experiment_id]

        ParticipantData.objects.update_or_create(
            participant=participant,
            content_type__model="experiment",
            object_id=experiment.id,
            team=request.team,
            defaults={"team": experiment.team, "data": new_data, "content_object": experiment},
        )
    return HttpResponse()
