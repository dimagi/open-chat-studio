"""Serializers for the read-only cost-tracking API endpoints."""

from rest_framework import serializers

from apps.cost_tracking.models import PricingRule


class CostSummarySerializer(serializers.Serializer):
    """Mirrors `services.reporting.CostSummary`."""

    period_start = serializers.DateTimeField()
    period_end = serializers.DateTimeField()
    total_cost = serializers.DecimalField(max_digits=14, decimal_places=8)
    previous_period_cost = serializers.DecimalField(max_digits=14, decimal_places=8)
    delta_pct = serializers.FloatField(allow_null=True)
    exact_cost = serializers.DecimalField(max_digits=14, decimal_places=8)
    estimated_cost = serializers.DecimalField(max_digits=14, decimal_places=8)
    unknown_call_count = serializers.IntegerField()
    last_synced = serializers.DateTimeField(allow_null=True)


class BotSpendSerializer(serializers.Serializer):
    """Mirrors `services.reporting.BotSpend`."""

    experiment_id = serializers.IntegerField()
    experiment_name = serializers.CharField()
    cost = serializers.DecimalField(max_digits=14, decimal_places=8)
    tokens = serializers.IntegerField()
    sessions = serializers.IntegerField()
    cost_per_session = serializers.DecimalField(max_digits=14, decimal_places=8, allow_null=True)


class UsageResponseSerializer(serializers.Serializer):
    summary = CostSummarySerializer()
    top_bots = BotSpendSerializer(many=True)


class PricingRuleSerializer(serializers.ModelSerializer):
    """One active PricingRule. Effective_to is implicit (only active rules
    are returned)."""

    class Meta:
        model = PricingRule
        fields = [
            "provider_type",
            "model_name",
            "service_kind",
            "unit_price",
            "currency",
            "source",
            "effective_from",
            "scope",
        ]

    scope = serializers.SerializerMethodField()

    def get_scope(self, obj: PricingRule) -> str:
        """team-scoped rules are 'team', everything else is 'global'."""
        return "team" if obj.team_id else "global"


class PricingResponseSerializer(serializers.Serializer):
    rules = PricingRuleSerializer(many=True)
