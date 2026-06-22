"""The two read endpoints the sync command consumes: the manifest (call order + per-model config)
and a generic, team-scoped, keyset-paginated resource endpoint. The manifest doubles as the
allowlist: the resource endpoint refuses any resource not listed in it."""

import base64
import json

from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import IsTeamAdmin
from apps.teams.sync.manifest import build_manifest, entry_model, get_entry, team_scoped_queryset
from apps.teams.sync.seal import load_public_key

from .serializers import build_sync_serializer

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


class ManifestView(APIView):
    permission_classes = [IsAuthenticated, IsTeamAdmin]

    def get(self, request):
        return Response(build_manifest())


class ResourceView(APIView):
    permission_classes = [IsAuthenticated, IsTeamAdmin]

    def get(self, request, resource):
        entry = get_entry(resource)
        if entry is None:
            raise NotFound("Unknown content type.")

        context = {"public_key": None}
        if entry.secret:
            if not request.team.public_key:
                return Response(
                    {"detail": "Team has no registered public key; secret data cannot be sealed."},
                    status=status.HTTP_409_CONFLICT,
                )
            context["public_key"] = load_public_key(request.team.public_key)

        limit = min(int(request.query_params.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
        queryset = team_scoped_queryset(entry, request.team)
        rows, next_cursor, has_more = _paginate(queryset, entry.cursor, request.query_params.get("cursor"), limit)

        serializer = build_sync_serializer(entry_model(entry.model))(rows, many=True, context=context)
        return Response({"cursor": next_cursor, "has_more": has_more, "results": serializer.data})


def _paginate(queryset, cursor_type, cursor, limit):
    if cursor_type == "pk":
        queryset = queryset.order_by("id")
        if cursor is not None:
            queryset = queryset.filter(id__gt=int(cursor))
        rows = list(queryset[: limit + 1])
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = str(rows[-1].id) if rows else cursor
        return rows, next_cursor, has_more

    queryset = queryset.order_by("updated_at", "id")
    if cursor:
        keyset = json.loads(base64.b64decode(cursor))
        timestamp = parse_datetime(keyset["updated_at"])
        queryset = queryset.filter(Q(updated_at__gt=timestamp) | Q(updated_at=timestamp, id__gt=keyset["id"]))
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    if rows:
        keyset = {"updated_at": rows[-1].updated_at.isoformat(), "id": rows[-1].id}
        next_cursor = base64.b64encode(json.dumps(keyset).encode()).decode()
    else:
        next_cursor = cursor
    return rows, next_cursor, has_more
