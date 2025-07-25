from django.contrib import admin

from .models import McpServer


@admin.register(McpServer)
class McpServerAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "created_at", "updated_at")
    search_fields = ("name",)
