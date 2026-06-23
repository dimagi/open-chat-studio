"""Django admin for cost tracking. Read-mostly surface for operator
debugging - adding a manual PricingRule for a model the auto-update
workflow can't reach, inspecting which UsageRecord rows landed for a
trace, etc. Day-to-day pricing edits happen through the team-scoped
override flow on the LLM Provider page.
"""

from django.contrib import admin

from apps.cost_tracking.models import PricingRule, UsageRecord


@admin.register(PricingRule)
class PricingRuleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "team",
        "provider_type",
        "model_name",
        "service_kind",
        "unit_price",
        "currency",
        "source",
        "effective_from",
        "effective_to",
    )
    list_filter = ("source", "service_kind", "currency", ("effective_to", admin.EmptyFieldListFilter))
    search_fields = ("provider_type", "model_name")
    readonly_fields = ("created_at", "updated_at", "created_by")
    raw_id_fields = ("team", "created_by")
    ordering = ("-effective_from",)


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "team",
        "timestamp",
        "provider_type",
        "model_name",
        "service_kind",
        "quantity",
        "cost",
        "currency",
        "confidence",
    )
    list_filter = ("confidence", "service_kind", "currency")
    search_fields = ("provider_type", "model_name")
    readonly_fields = (
        "team",
        "timestamp",
        "service_kind",
        "provider_type",
        "model_name",
        "quantity",
        "unit_price",
        "cost",
        "currency",
        "confidence",
        "experiment",
        "session",
        "participant",
        "trace",
        "pricing_rule",
        "extra",
    )
    raw_id_fields = ("team", "experiment", "session", "participant", "trace", "pricing_rule")
    ordering = ("-timestamp",)

    def has_add_permission(self, request):
        """UsageRecord is system-written from the tracer; admins shouldn't
        hand-author rows."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Cost history is audit data. Deletions would corrupt billing
        evidence and the digest's coverage-gap reports."""
        return False
