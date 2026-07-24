from drf_spectacular.utils import PolymorphicProxySerializer, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.pagination import CursorPagination
from apps.api.v2.usage import services
from apps.api.v2.usage.param_serializers import UsageQuerySerializer
from apps.api.v2.usage.permissions import CanViewUsage
from apps.api.v2.usage.serializers import (
    GroupedUsageResponseSerializer,
    GroupedUsageRowSerializer,
    UsagePeriodSerializer,
    UsageResponseSerializer,
)
from apps.api.v2.usage.services import GROUP_PLATFORM, UsageQuery, usage_query
from apps.oauth.permissions import TokenHasOAuthResourceScope


class PlatformCursorPagination(CursorPagination):
    # The platform breakdown has no backing model (so no ``created_at``); it paginates the distinct
    # platform slugs, which are unique and orderable, so cursor over the ``platform`` field instead.
    ordering = "platform"


class UsageView(APIView):
    # A comment, not a docstring: drf-spectacular would publish a docstring as the operation
    # description, and the per-operation description below is the client-facing one.
    # Team-scoped usage inspection. Returns message counts, session counts, distinct participant
    # counts, cost, and token counts over a time window, optionally bucketed, grouped by a dimension,
    # and narrowed by participant/chatbot/platform.
    # See docs/design/usage-api.md.

    permission_classes = [IsAuthenticated, CanViewUsage, TokenHasOAuthResourceScope]
    required_scopes = ["usage"]

    @extend_schema(
        operation_id="usage",
        summary="Usage",
        description=(
            "Return team-scoped usage data for a time window. The window is either a calendar 'period' "
            "(current/previous month) or an explicit half-open 'start'/'end'. Each requested metric "
            "gets its own block: 'messages' (human/AI/total counts), 'sessions' (count), 'participants' "
            "(distinct count), 'cost' (total spend and currency), and 'tokens' (prompt/completion/total). "
            "With 'granularity' finer than 'total', results are one row per time bucket. With 'group_by' "
            "set, results are cursor-paginated breakdown rows — one per group, or one per (group, bucket) "
            "when combined with a finer granularity. Optionally narrowed by participant, chatbot, or "
            "platform."
        ),
        tags=["Usage"],
        # Query parameters are derived from the request serializer so the docs can't drift from validation.
        parameters=[UsageQuerySerializer],
        responses=PolymorphicProxySerializer(
            component_name="UsageResponseOrGrouped",
            serializers=[UsageResponseSerializer, GroupedUsageResponseSerializer],
            resource_type_field_name=None,
        ),
    )
    def get(self, request):
        params = UsageQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        validated = params.validated_data
        query = UsageQuery(
            team=request.team,
            metrics=validated["metric"],
            start=validated["start"],
            end=validated["end"],
            granularity=validated["granularity"],
            tz=validated["tz"],
            participant=validated.get("participant"),
            participant_identifier=validated.get("participant_identifier"),
            chatbot=validated.get("chatbot"),
            platform=validated.get("platform"),
            group_by=validated.get("group_by"),
        )
        # Resolve the participant/chatbot handles to FK ids once, so every metric filters on ids.
        query = services.resolve_query_filters(query)
        if query.group_by:
            return self._grouped_response(request, query)
        result = usage_query(query)
        return Response(UsageResponseSerializer(result).data)

    def _grouped_response(self, request, query: UsageQuery) -> Response:
        """Cursor-paginate the groups, compute each page's metrics, and wrap the rows in the shared
        pagination envelope augmented with the request's ``period`` and ``group_by``."""
        paginator = PlatformCursorPagination() if query.group_by == GROUP_PLATFORM else CursorPagination()
        # Each group expands to one row per time bucket, so bound groups-per-page by the bucket count to
        # keep the materialised page (groups × buckets) from ballooning at a fine granularity. Clamp both
        # the client cutoff (max_page_size) and the default (page_size): DRF only applies max_page_size
        # when the client sends ?page_size, so a request that omits it would otherwise fall through to the
        # unclamped project default and defeat the cap.
        cap = services.grouped_page_size_cap(query)
        paginator.max_page_size = min(paginator.max_page_size, cap)
        paginator.page_size = min(paginator.page_size, cap)
        page = paginator.paginate_queryset(services.group_entities(query), request, view=self)
        rows = services.group_rows(query, page)
        response = paginator.get_paginated_response(GroupedUsageRowSerializer(rows, many=True).data)
        response.data["period"] = UsagePeriodSerializer(
            {"period_start": query.start, "period_end": query.end, "timezone": str(query.tz)}
        ).data
        response.data["group_by"] = query.group_by
        return response
