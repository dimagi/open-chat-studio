from typing import Dict

from django.contrib import admin
from django.http.request import HttpRequest

from apps.channels.models import ChannelSession, ExperimentChannel


@admin.register(ExperimentChannel)
class ExperimentChannelAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "platform",
        "active",
    )
    search_fields = ("name",)
    list_filter = (
        "created_at",
        "updated_at",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )

    def get_changeform_initial_data(self, request: HttpRequest) -> Dict[str, str]:
        return {"extra_data": {"bot_token": "your token here"}}

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.exclude(platform="web")


@admin.register(ChannelSession)
class ChannelSessionAdmin(admin.ModelAdmin):
    readonly_fields = (
        "created_at",
        "updated_at",
    )
