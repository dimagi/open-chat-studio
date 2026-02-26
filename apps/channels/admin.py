from django.contrib import admin
from django.http.request import HttpRequest

from apps.channels.models import ExperimentChannel


@admin.register(ExperimentChannel)
class ExperimentChannelAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "platform", "deleted", "external_id", "messaging_provider")
    search_fields = ("name", "external_id")
    list_filter = (
        "platform",
        "created_at",
        "updated_at",
    )
    readonly_fields = (
        "external_id",
        "created_at",
        "updated_at",
    )

    @admin.display(description="Messaging Provider")
    def messaging_provider(self, obj):
        if obj.messaging_provider:
            return obj.messaging_provider.name

    def get_changeform_initial_data(self, request: HttpRequest) -> dict:
        return {"extra_data": {"bot_token": "your token here"}}

    def get_queryset(self, *args, **kwargs):
        return ExperimentChannel.objects.get_unfiltered_queryset()
