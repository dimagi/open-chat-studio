from uuid import UUID

from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.generics import ListAPIView

from apps.api.permissions import HasUserAPIKey
from apps.chat.bots import TopicBot
from apps.chat.models import ChatMessage, ChatMessageType
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


@api_view(["POST"])
@permission_classes([HasUserAPIKey])
@permission_required("experiments.change_participantdata")
@transaction.atomic()
def new_session(request, experiment_id: UUID):
    """
    Expected body:
    {
        "ephemeral": true,
        "user_input": "",
        "history" = [
            {"type": "human", "message": "Hi there"},
            {"type": "ai", "message": "Hi, how can I assist you today?"}
        ]
    }
    """
    history_messages = request.data["history"]
    user_input = request.data["user_input"]
    is_ephemeral = request.data.get("ephemeral", False)
    experiment = get_object_or_404(Experiment, public_id=experiment_id, team=request.team)
    participant, _created = Participant.objects.get_or_create(
        identifier=request.user.email, team=request.team, user=request.user
    )
    session = experiment.new_api_session(participant)
    chat_messages = []
    for message_data in history_messages:
        message_type, content = message_data.values()
        if message_type not in ChatMessageType.values:
            return JsonResponse({"error": f"Unknown message type '{message_type}'"}, status=422)

        chat_messages.append(
            ChatMessage(chat=session.chat, message_type=ChatMessageType(message_type), content=content)
        )
    ChatMessage.objects.bulk_create(chat_messages)

    experiment_bot = TopicBot(session)
    response = experiment_bot.process_input(user_input)

    session_id = session.external_id
    if is_ephemeral:
        session.delete()
        session_id = None

    return JsonResponse(data={"session_id": session_id, "response": response})
