from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.api.permissions import HasUserAPIKey
from apps.experiments.models import Experiment


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


# @api_view(["POST"])
# @permission_classes([HasUserAPIKey])
# def update_participant_details(request):
#     """
#     {
#         "participants": [
#             {"participant_id": "1234", "experiment_public_id": "", "details": {}}
#         ]
#     }
#     """
#     data = request.POST.dict()
#     participant_entries = data["participants"]
#     for entry in participant_entries:
#         experiment_public_id = entry["experiment_public_id"]
#         identifier = entry["participant_id"]
#         experiment = get_object_or_404(Experiment, public_id=experiment_public_id)
#         participant = get_object_or_404(participant, identifier=identifier)
#         participant_data = ParticipantData.objects.filter(participant=participant, object=experiment)
#         if participant_data:
#             participant_data = participant_data.data | entry["details"]
#             participant_data.save()
#         else:
#             ParticipantData.objects.create(participant=participant, object=experiment, data=data)
#     return Response(data=data)
