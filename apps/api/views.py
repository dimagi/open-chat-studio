from django.contrib.auth.decorators import permission_required
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.generics import ListAPIView
from rest_framework.response import Response

from apps.api.permissions import HasUserAPIKey
from apps.experiments.models import Experiment, Participant, ParticipantData

require_view_experiment = permission_required("experiments.view_experiment")


class ExperimentSerializer(serializers.Serializer):
    name = serializers.CharField()
    experiment_id = serializers.UUIDField(source="public_id")


@method_decorator(require_view_experiment, name="get")
class ExperimentsView(ListAPIView):
    http_method_names = ["get"]
    permission_classes = [HasUserAPIKey]
    serializer_class = ExperimentSerializer

    def get_queryset(self):
        return Experiment.objects.filter(team__slug=self.request.team.slug).all()


@api_view(["POST"])
@permission_classes([HasUserAPIKey])
@permission_required("experiments.change_participantdata")
def update_participant_data(request, participant_id: str):
    """
    Upsert participant data for a specific experiment
    """
    experiment_data = request.data
    experiment_ids = experiment_data.keys()
    experiments = Experiment.objects.filter(public_id__in=experiment_ids, team=request.team)
    experiment_map = {str(experiment.public_id): experiment for experiment in experiments}
    participant = get_object_or_404(Participant, identifier=participant_id, team=request.team)

    experiments_not_updated = []
    for experiment_id, new_data in experiment_data.items():
        if experiment_id not in experiment_map:
            experiments_not_updated.append(experiment_id)
            continue

        experiment = experiment_map[experiment_id]

        ParticipantData.objects.update_or_create(
            participant=participant,
            content_type__model="experiment",
            object_id=experiment.id,
            team=request.team,
            defaults={"team": experiment.team, "data": new_data, "content_object": experiment},
        )
    response_body = ""
    if experiments_not_updated:
        response_body = {"unsuccessful_updates": experiments_not_updated}
    return Response(data=response_body)
