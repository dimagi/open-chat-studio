from django.contrib import admin
from field_audit.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ["event_date", "object_class_path", "is_create", "is_delete"]
    list_filter = ["event_date", "object_class_path", "is_create", "is_delete"]
    date_hierarchy = "event_date"
    ordering = ["-event_date"]
