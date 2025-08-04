from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin

from .models import McpServer


@admin.register(McpServer)
class McpServerAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "team", "created_at", "updated_at")
    search_fields = ("name",)
