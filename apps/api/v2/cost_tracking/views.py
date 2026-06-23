"""Read-only cost-tracking API endpoints. Both views are flag-gated: when
`flag_ai_cost_monitoring` is off for the calling team the routes return 404
so the cost surface is invisible to non-opted-in teams.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.v2.cost_tracking.serializers import (
    PricingResponseSerializer,
    PricingRuleSerializer,
    UsageResponseSerializer,
)
from apps.cost_tracking.models import PricingRule
from apps.cost_tracking.services.reporting import cost_summary, top_n_bots
from apps.oauth.permissions import TokenHasOAuthResourceScope
from apps.teams.models import Flag

COST_TRACKING_FLAG = "flag_ai_cost_monitoring"
DEFAULT_PERIOD_DAYS = 30


class CostTrackingUsageView(APIView):
    """Period-over-period spend summary plus top-N experiments for the team."""

    permission_classes = [IsAuthenticated, TokenHasOAuthResourceScope]
    required_scopes = ["cost_tracking"]

    @extend_schema(
        operation_id="cost_tracking_usage",
        summary="Cost-tracking usage",
        tags=["Cost Tracking"],
        parameters=[
            OpenApiParameter(
                name="start",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="ISO 8601 start of the reporting window (defaults to 30 days before `end`).",
            ),
            OpenApiParameter(
                name="end",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="ISO 8601 end of the reporting window (defaults to now).",
            ),
        ],
        responses={200: UsageResponseSerializer},
    )
    def get(self, request: Request) -> Response:
        _require_flag(request.team)
        start, end = _parse_period(request.query_params)
        summary = cost_summary(request.team, start=start, end=end)
        bots = top_n_bots(request.team, start=start, end=end)
        return Response(
            UsageResponseSerializer({"summary": summary.__dict__, "top_bots": [b.__dict__ for b in bots]}).data
        )


class CostTrackingPricingView(APIView):
    """Active PricingRules visible to the caller's team (team overrides
    plus globals). Read-only snapshot."""

    permission_classes = [IsAuthenticated, TokenHasOAuthResourceScope]
    required_scopes = ["cost_tracking"]

    @extend_schema(
        operation_id="cost_tracking_pricing",
        summary="Cost-tracking pricing",
        tags=["Cost Tracking"],
        responses={200: PricingResponseSerializer},
    )
    def get(self, request: Request) -> Response:
        _require_flag(request.team)
        rules = PricingRule.objects.filter(
            Q(team=request.team) | Q(team__isnull=True),
            effective_to__isnull=True,
        ).order_by("provider_type", "model_name", "service_kind", "-team_id")
        return Response({"rules": PricingRuleSerializer(rules, many=True).data})


def _require_flag(team) -> None:
    """Return 404 (not 403) when the flag is off so the surface is hidden
    from teams that haven't opted in."""
    if not Flag.get(COST_TRACKING_FLAG).is_active_for_team(team):
        raise NotFound("Cost tracking is not enabled for this team.")


def _parse_period(params) -> tuple[datetime, datetime]:
    try:
        end = _parse_iso(params.get("end"), default=timezone.now())
        start = _parse_iso(params.get("start"), default=end - timedelta(days=DEFAULT_PERIOD_DAYS))
    except ValueError as exc:
        raise ValidationError({"detail": f"Invalid date format: {exc}"}) from exc
    if start > end:
        raise ValidationError({"detail": "`start` must be less than or equal to `end`."})
    return start, end


def _parse_iso(value: str | None, *, default: datetime) -> datetime:
    """ISO 8601 parser that always returns a timezone-aware datetime so
    Django ORM filters don't blow up under `USE_TZ=True`. Naive inputs are
    interpreted as UTC."""
    if not value:
        return default
    parsed = datetime.fromisoformat(value)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed
