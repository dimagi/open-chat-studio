from django.contrib import admin
from field_audit.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = [
        "event_date",
        "object_class_path",
        "object_pk",
        "username",
        "transaction_id",
        "is_create",
        "is_delete",
    ]
    list_filter = ["event_date", "is_create", "is_delete", "object_class_path"]
    date_hierarchy = "event_date"
    ordering = ["-event_date"]
    search_fields = ["object_class_path", "change_context"]

    def transaction_id(self, obj):
        return obj.change_context.get("transaction_id", "")

    def username(self, obj):
        return obj.change_context.get("username", "")

    def get_search_results(self, request, queryset, search_term):
        try:
            search_term_as_int = int(search_term)
        except ValueError:
            pass
        else:
            return queryset.filter(object_pk=search_term_as_int), False

        return super().get_search_results(request, queryset, search_term)
