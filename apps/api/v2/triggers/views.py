"""A chatbot's triggers: static, timeout and scheduled (#4144).

Triggers belong to the chatbot rather than its pipeline. Every write targets the working (draft)
chatbot's own triggers; a published version keeps the triggers it was published with until the
chatbot is published again.
"""

from django.db import models, transaction
from rest_framework import status
from rest_framework.generics import GenericAPIView, get_object_or_404
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response

from apps.api.permissions import BASE_PERMISSION_CLASSES
from apps.api.v2.lookups import get_working_chatbot
from apps.api.v2.write.base import ChatbotCompositionPermission, ChatbotReadPermission, DescribesPatch
from apps.api.v2.write.serializers import ResourceArchivedSerializer
from apps.events.models import ScheduledTrigger, StaticTrigger, TimeoutTrigger
from apps.experiments.models import Experiment
from apps.oauth.permissions import TokenHasOAuthResourceScope, enforce_application_chatbot_write

from .schema import (
    scheduled_trigger_list_schema,
    scheduled_trigger_schema,
    static_trigger_list_schema,
    static_trigger_schema,
    timeout_trigger_list_schema,
    timeout_trigger_schema,
)
from .serializers import (
    BaseTriggerSerializer,
    ScheduledTriggerWriteSerializer,
    StaticTriggerWriteSerializer,
    TimeoutTriggerWriteSerializer,
)


class TriggerView(GenericAPIView):
    """List, read, create, update and archive one kind of trigger on a working chatbot.

    Each kind has one subclass per route, so each route's GET gets its own OpenAPI operation, and each
    ``path()`` narrows ``http_method_names`` to the verbs its route offers.
    """

    required_scopes = ["chatbots"]
    metadata_class = DescribesPatch
    serializer_class: type[BaseTriggerSerializer]
    model: type[models.Model]
    # Only here so the generic view has a queryset; permissions are not derived from it.
    queryset = Experiment.objects.none()

    chatbot: Experiment | None = None

    def get_permissions(self):
        chatbot_permission = (
            ChatbotReadPermission if self.request.method in SAFE_METHODS else ChatbotCompositionPermission
        )
        return [
            permission() for permission in [*BASE_PERMISSION_CLASSES, chatbot_permission, TokenHasOAuthResourceScope]
        ]

    def get_serializer_context(self) -> dict:
        return {**super().get_serializer_context(), "chatbot": self.chatbot}

    def get(self, request, id: str, trigger_id: int | None = None) -> Response:
        self.chatbot = self._chatbot(id)
        triggers = self._working_triggers().select_related("action")
        if trigger_id is None:
            return Response(self.get_serializer(triggers.order_by("id"), many=True).data)
        return Response(self.get_serializer(get_object_or_404(triggers, id=trigger_id)).data)

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
        if self.request.method not in SAFE_METHODS:
            enforce_application_chatbot_write(self.request, chatbot)
        return chatbot

    def _working_triggers(self) -> models.QuerySet:
        return self.model.objects.working_versions_queryset().filter(experiment=self.chatbot)


@static_trigger_list_schema
class StaticTriggerListView(TriggerView):
    serializer_class = StaticTriggerWriteSerializer
    model = StaticTrigger


@static_trigger_schema
class StaticTriggerView(StaticTriggerListView):
    pass


@timeout_trigger_list_schema
class TimeoutTriggerListView(TriggerView):
    serializer_class = TimeoutTriggerWriteSerializer
    model = TimeoutTrigger


@timeout_trigger_schema
class TimeoutTriggerView(TimeoutTriggerListView):
    pass


@scheduled_trigger_list_schema
class ScheduledTriggerListView(TriggerView):
    serializer_class = ScheduledTriggerWriteSerializer
    model = ScheduledTrigger


@scheduled_trigger_schema
class ScheduledTriggerView(ScheduledTriggerListView):
    pass
