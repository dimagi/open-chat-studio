"""A chatbot's triggers: static, timeout and scheduled (#4144).

Triggers belong to the chatbot rather than its pipeline. Every write targets the working (draft)
chatbot's own triggers; a published version keeps the triggers it was published with until the
chatbot is published again. Read them back with the `chatbot_inspect` endpoint.
"""

from django.db import models, transaction
from rest_framework import status
from rest_framework.generics import GenericAPIView, get_object_or_404
from rest_framework.response import Response

from apps.api.permissions import BASE_PERMISSION_CLASSES
from apps.api.v2.lookups import get_working_chatbot
from apps.api.v2.write.base import ChatbotCompositionPermission, DescribesPatch
from apps.api.v2.write.serializers import ResourceArchivedSerializer
from apps.events.models import ScheduledTrigger, StaticTrigger, TimeoutTrigger
from apps.experiments.models import Experiment
from apps.oauth.permissions import TokenHasOAuthResourceScope, enforce_application_chatbot_write

from .schema import scheduled_trigger_schema, static_trigger_schema, timeout_trigger_schema
from .serializers import (
    BaseTriggerSerializer,
    ScheduledTriggerWriteSerializer,
    StaticTriggerWriteSerializer,
    TimeoutTriggerWriteSerializer,
)


class TriggerView(GenericAPIView):
    """Create, update and archive one kind of trigger on a working chatbot.

    Serves both routes, so each ``path()`` narrows ``http_method_names`` to the verbs it offers.
    """

    permission_classes = [*BASE_PERMISSION_CLASSES, ChatbotCompositionPermission, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]
    metadata_class = DescribesPatch
    serializer_class: type[BaseTriggerSerializer]
    model: type[models.Model]
    # Only here so the generic view has a queryset; permissions are not derived from it.
    queryset = Experiment.objects.none()

    chatbot: Experiment | None = None

    def get_serializer_context(self) -> dict:
        return {**super().get_serializer_context(), "chatbot": self.chatbot}

    def post(self, request, id: str) -> Response:
        self.chatbot = self._chatbot(id)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trigger = serializer.save()
        return Response(self.get_serializer(trigger).data, status=status.HTTP_201_CREATED)

    def patch(self, request, id: str, trigger_id: int) -> Response:
        self.chatbot = self._chatbot(id)
        with transaction.atomic():
            # Locked so that two PATCHes, each completing the body from the stored trigger, cannot
            # each overwrite the other's change.
            trigger = get_object_or_404(
                self._working_triggers().select_related("action").select_for_update(), id=trigger_id
            )
            serializer = self.get_serializer(trigger, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            trigger = serializer.save()
        return Response(self.get_serializer(trigger).data)

    def delete(self, request, id: str, trigger_id: int) -> Response:
        self.chatbot = self._chatbot(id)
        with transaction.atomic():
            get_object_or_404(self._working_triggers().select_for_update(), id=trigger_id).archive()
        return Response(ResourceArchivedSerializer({"archived": True}).data)

    def _chatbot(self, public_id: str) -> Experiment:
        chatbot = get_working_chatbot(self.request.team, public_id)
        enforce_application_chatbot_write(self.request, chatbot)
        return chatbot

    def _working_triggers(self) -> models.QuerySet:
        return self.model.objects.working_versions_queryset().filter(experiment=self.chatbot)


@static_trigger_schema
class StaticTriggerView(TriggerView):
    serializer_class = StaticTriggerWriteSerializer
    model = StaticTrigger


@timeout_trigger_schema
class TimeoutTriggerView(TriggerView):
    serializer_class = TimeoutTriggerWriteSerializer
    model = TimeoutTrigger


@scheduled_trigger_schema
class ScheduledTriggerView(TriggerView):
    serializer_class = ScheduledTriggerWriteSerializer
    model = ScheduledTrigger
