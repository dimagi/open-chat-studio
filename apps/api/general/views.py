"""The two read endpoints the sync command consumes: the manifest (call order + per-model config)
and a generic, team-scoped, keyset-paginated resource endpoint. The manifest doubles as the
allowlist: the resource endpoint refuses any resource not listed in it. ``resource_view`` is the
factory the URLConf uses to mount one documented copy of the generic endpoint per resource."""

import base64
import json

from django.db.models import Q
from django.utils.dateparse import parse_datetime
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import IsTeamAdmin
from apps.teams.sync.manifest import (
    ManifestEntry,
    build_manifest,
    entry_model,
    get_manifest_entry,
    team_scoped_queryset,
)
from apps.teams.sync.seal import load_public_key

from .schema import resource_responses
from .serializers import ManifestSerializer, build_sync_serializer

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


class ManifestView(APIView):
    permission_classes = [IsAuthenticated, IsTeamAdmin]

    @extend_schema(responses=ManifestSerializer)
    def get(self, request):
        return Response(build_manifest())


class ResourceView(APIView):
    # The schema for this view is generated in apps/api/general/schema.py
    permission_classes = [IsAuthenticated, IsTeamAdmin]

    def get(self, request, resource):
        entry = get_manifest_entry(resource)
        if entry is None:
            # Defense-in-depth: routing only mounts manifested resources, so this can only fire
            # if the view is called directly (e.g. in tests) with a resource not in the manifest.
            raise NotFound("Unknown resource.")

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


class CursorHelper:
    """Encode/decode the keyset-pagination cursor as a base64-wrapped JSON blob."""

    @staticmethod
    def encode(keyset: dict) -> str:
        return base64.b64encode(json.dumps(keyset).encode()).decode()

    @staticmethod
    def decode(cursor: str) -> dict:
        return json.loads(base64.b64decode(cursor))


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
        keyset = CursorHelper.decode(cursor)
        timestamp = parse_datetime(keyset["updated_at"])
        queryset = queryset.filter(Q(updated_at__gt=timestamp) | Q(updated_at=timestamp, id__gt=keyset["id"]))
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    if rows:
        next_cursor = CursorHelper.encode({"updated_at": rows[-1].updated_at.isoformat(), "id": rows[-1].id})
    else:
        next_cursor = cursor
    return rows, next_cursor, has_more


# --- OpenAPI documentation -------------------------------------------------------------------------
# OpenAPI can't vary a response by the *value* of a path parameter, so the URLConf mounts one literal
# path per synced resource (see ``apps/api/v2/urls.py``), each routed to a ResourceView subclass that
# ``resource_view`` decorates with that resource's response schema. The response serializers
# themselves are built in ``schema.py``.

_QUERY_PARAMETERS: list[OpenApiParameter] = [
    OpenApiParameter(
        name="cursor",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Keyset pagination cursor returned as `cursor` by the previous page.",
    ),
    OpenApiParameter(
        name="limit",
        type=OpenApiTypes.INT,
        location=OpenApiParameter.QUERY,
        required=False,
        description=f"Maximum rows per page (default {DEFAULT_LIMIT}, capped at {MAX_LIMIT}).",
    ),
]


def resource_view(entry: ManifestEntry) -> type[ResourceView]:
    """A ResourceView subclass documenting a single resource, for the URLConf to mount at that
    resource's literal path. Subclassing keeps the auth and permissions (and therefore the security
    schemes) identical to the base view; the subclass exists only to carry the per-resource schema."""
    model = entry_model(entry.model)
    view = type(f"{model.__name__}ExportView", (ResourceView,), {})
    return extend_schema_view(
        get=extend_schema(
            operation_id=f"sync_{entry.resource}",
            summary=entry.resource.replace("_", " ").title(),
            tags=["Export"],
            parameters=_QUERY_PARAMETERS,
            responses=resource_responses(entry),
        )
    )(view)


@extend_schema_view(get=extend_schema(exclude=True))
class UnknownResourceView(ResourceView):
    """Catch-all that returns a JSON 404 for any resource not in the manifest.

    Excluded from the OpenAPI schema; per-resource paths document the full API surface.
    """
