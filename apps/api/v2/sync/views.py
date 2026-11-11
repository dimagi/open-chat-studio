import hashlib
import json
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.annotations.models import CustomTaggedItem
from apps.annotations.prefetch import attach_chat_tagged_items
from apps.api.keyset import encode_cursor, keyset_page
from apps.api.permissions import BASE_PERMISSION_CLASSES
from apps.api.v2.sync.permissions import CanViewMessages, CanViewSessions
from apps.api.v2.sync.serializers import (
    MessagePageSerializer,
    MessageQuerySerializer,
    MessageSerializer,
    SessionDetailSerializer,
    SessionPageSerializer,
    SessionQuerySerializer,
    SessionSerializer,
)
from apps.chat.models import Chat, ChatMessage
from apps.experiments.models import ExperimentSession
from apps.oauth.permissions import TokenHasOAuthResourceScope, token_reaches_all_data

# `updated_at` is stamped when a row is saved, not when its transaction commits. Holding back the newest
# rows keeps a slow commit from landing behind a cursor a consumer has already passed.
SYNC_SETTLE_WINDOW = timedelta(seconds=60)


class SyncView(APIView):
    # A comment, not a docstring: drf-spectacular reads a view docstring as the operation description.
    # Shared auth and the page shape for the sync collections (ADR-0072).
    permission_classes = [*BASE_PERMISSION_CLASSES, CanViewSessions, TokenHasOAuthResourceScope]
    required_scopes = ["sessions"]

    def page(self, queryset, params, serializer_class) -> Response:
        queryset = queryset.filter(updated_at__lte=timezone.now() - SYNC_SETTLE_WINDOW)
        scope = self.selection(params)
        rows, has_more = keyset_page(queryset, params.get("cursor"), params["limit"], scope)
        cursor = encode_cursor(rows[-1], scope) if rows else params.get("cursor")
        results = serializer_class(self.prepare(rows), many=True, context={"request": self.request}).data
        return Response({"cursor": cursor, "has_more": has_more, "results": results})

    def prepare(self, rows: list) -> list:
        return rows

    def selection(self, params) -> str:
        """Identifies the rows a cursor walks: the filters and which chatbots the caller reaches."""
        filters = {
            key: sorted(value) if isinstance(value, list) else value
            for key, value in params.items()
            if key not in ("cursor", "limit")
        }
        reach = None
        if not token_reaches_all_data(self.request.auth):
            reach = sorted(self.request.auth.application.allowed_chatbots.values_list("id", flat=True))
        selection = json.dumps([filters, reach], sort_keys=True, default=str)
        return hashlib.sha256(selection.encode()).hexdigest()[:16]

    def reachable_sessions(self):
        sessions = ExperimentSession.objects.filter(team=self.request.team)
        if not token_reaches_all_data(self.request.auth):
            sessions = sessions.filter(experiment_id__in=self.request.auth.application.allowed_chatbots.values("id"))
        return sessions

    def reachable_messages(self):
        return ChatMessage.objects.filter(chat__experiment_session__in=self.reachable_sessions())


class SessionListView(SyncView):
    @extend_schema(
        operation_id="session_list",
        summary="List sessions changed since a cursor",
        description=(
            "Sessions in the order they last changed. Store `cursor` from each response and send it on the "
            "next pull to receive only sessions created or changed since. Rows changed in the last minute "
            "are returned on a later pull."
        ),
        tags=["Sessions"],
        parameters=[SessionQuerySerializer],
        responses=SessionPageSerializer,
    )
    def get(self, request):
        query = SessionQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data

        sessions = self.reachable_sessions().select_related("experiment", "participant", "chat")
        if "chatbot" in params:
            sessions = sessions.filter(experiment__public_id=params["chatbot"])
        if "participant" in params:
            sessions = sessions.filter(participant__public_id=params["participant"])
        if "platform" in params:
            sessions = sessions.filter(platform=params["platform"])
        if params.get("tags"):
            sessions = sessions.filter(_chat_has_tag(params["tags"], request.team))
        return self.page(sessions, params, SessionSerializer)

    def prepare(self, rows: list) -> list:
        return attach_chat_tagged_items(rows)


class MessageListView(SyncView):
    permission_classes = [*BASE_PERMISSION_CLASSES, CanViewMessages, TokenHasOAuthResourceScope]

    @extend_schema(
        operation_id="message_list",
        summary="List messages changed since a cursor",
        description=(
            "Messages across the team's sessions, in the order they last changed. Store `cursor` from each "
            "response and send it on the next pull to receive only messages created or changed since. Rows "
            "changed in the last minute are returned on a later pull."
        ),
        tags=["Sessions"],
        parameters=[MessageQuerySerializer],
        responses=MessagePageSerializer,
    )
    def get(self, request):
        query = MessageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data

        messages = self.reachable_messages().select_related("chat__experiment_session").prefetch_related("tags")
        if "chatbot" in params:
            messages = messages.filter(chat__experiment_session__experiment__public_id=params["chatbot"])
        if "session" in params:
            messages = messages.filter(chat__experiment_session__external_id=params["session"])
        if "participant" in params:
            messages = messages.filter(chat__experiment_session__participant__public_id=params["participant"])
        return self.page(messages, params, MessageSerializer)

    def prepare(self, rows: list) -> list:
        ChatMessage.prefetch_attached_files(rows)
        return rows


class SessionDetailView(SyncView):
    @extend_schema(
        operation_id="session_retrieve",
        summary="Retrieve a session with its messages",
        description="One session and all of its messages, oldest first.",
        tags=["Sessions"],
        responses=SessionDetailSerializer,
    )
    def get(self, request, id):
        messages = ChatMessage.objects.order_by("created_at", "id").prefetch_related("tags")
        session = get_object_or_404(
            self.reachable_sessions()
            .select_related("experiment", "participant", "chat")
            .prefetch_related(Prefetch("chat__messages", queryset=messages, to_attr="synced_messages")),
            external_id=id,
        )
        attach_chat_tagged_items([session])
        ChatMessage.prefetch_attached_files(session.chat.synced_messages)
        return Response(SessionDetailSerializer(session, context={"request": request}).data)


def _chat_has_tag(names: list[str], team) -> Exists:
    return Exists(
        CustomTaggedItem.objects.filter(
            content_type=ContentType.objects.get_for_model(Chat),
            object_id=OuterRef("chat_id"),
            tag__name__in=names,
            tag__team=team,
        )
    )
