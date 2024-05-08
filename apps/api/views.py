from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.api.permissions import HasUserAPIKey
from apps.experiments.models import Experiment, Participant, ParticipantData


class ExperimentSerializer(serializers.Serializer):
    name = serializers.CharField()
    experiment_id = serializers.UUIDField(source="public_id")


@api_view(["GET"])
@permission_classes([HasUserAPIKey])
def get_experiments(request):
    data = []
    for experiment in Experiment.objects.filter(team__slug=request.team.slug).all():
        data.append(ExperimentSerializer(experiment).data)
    return Response(data=data)


@api_view(["POST"])
@permission_classes([HasUserAPIKey])
def update_participant_data(request):
    """
    Upsert participant data for a specific experiment
    """
    experiment_public_id = request.data["experiment_id"]
    identifier = request.data["participant_id"]
    new_data = request.data["details"]
    experiment = get_object_or_404(Experiment, public_id=experiment_public_id)
    participant = get_object_or_404(Participant, identifier=identifier, team=experiment.team)

    try:
        participant_data = experiment.participant_data.get(participant=participant)
        participant_data.data = new_data
        participant_data.save(update_fields=["data"])
    except ParticipantData.DoesNotExist:
        ParticipantData.objects.create(
            participant=participant, data=new_data, content_object=experiment, team=experiment.team
        )
    return HttpResponse()
