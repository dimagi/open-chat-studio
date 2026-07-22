from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.v2.usage.param_serializers import UsageQuerySerializer
from apps.api.v2.usage.permissions import CanViewUsage
from apps.api.v2.usage.serializers import UsageResponseSerializer
from apps.api.v2.usage.services import usage_query
from apps.oauth.permissions import TokenHasOAuthResourceScope


class UsageView(APIView):
    # A comment, not a docstring: drf-spectacular would publish a docstring as the operation
    # description, and the per-operation description below is the client-facing one.
    # Team-scoped usage inspection. Returns message counts, session counts, and distinct participant
    # counts for a calendar month, optionally narrowed to a single participant.
    # See docs/design/usage-api.md.

    permission_classes = [IsAuthenticated, CanViewUsage, TokenHasOAuthResourceScope]
    required_scopes = ["usage"]

    @extend_schema(
        operation_id="usage",
        summary="Usage",
        description=(
            "Return team-scoped usage data for a calendar month. Each requested metric gets its own "
            "block: 'messages' (human/AI/total counts), 'sessions' (count), and 'participants' "
            "(distinct count). Optionally narrowed to a single participant."
        ),
        tags=["Usage"],
        # Query parameters are derived from the request serializer so the docs can't drift from validation.
        parameters=[UsageQuerySerializer],
        responses=UsageResponseSerializer,
    )
    def get(self, request):
        params = UsageQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        validated = params.validated_data
        result = usage_query(
            request.team,
            metrics=validated["metric"],
            period=validated["period"],
            tz=validated["tz"],
            participant=validated.get("participant"),
            participant_identifier=validated.get("participant_identifier"),
        )
        return Response(UsageResponseSerializer(result).data)
