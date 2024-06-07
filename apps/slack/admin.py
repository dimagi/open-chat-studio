from django.contrib import admin

from apps.slack.models import SlackInstallation


@admin.register(SlackInstallation)
class SlackInstallationAdmin(admin.ModelAdmin):
    list_display = ["slack_team_name", "team", "created_at", "slack_team_id"]
