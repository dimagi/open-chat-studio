from django.contrib import admin

from apps.slack.models import SlackInstallation
from apps.utils.admin import ReadonlyAdminMixin


@admin.register(SlackInstallation)
class SlackInstallationAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ["slack_team_name", "created_at", "slack_team_id"]
