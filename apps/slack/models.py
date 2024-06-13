from django.db import models

from apps.slack.const import INSTALLATION_CONFIG
from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel


class SlackBot(BaseModel):
    """Copied from the example app:
    https://github.com/slackapi/bolt-python/tree/main/examples/django/oauth_app
    """

    client_id = models.CharField(null=False, max_length=32)
    app_id = models.CharField(null=False, max_length=32)
    enterprise_id = models.CharField(null=True, max_length=32)  # noqa: DJ001
    enterprise_name = models.TextField(null=True)  # noqa: DJ001
    # renamed to avoid conflict with team_id from base model
    slack_team_id = models.CharField(null=True, max_length=32)  # noqa: DJ001
    slack_team_name = models.TextField(null=True)  # noqa: DJ001
    bot_token = models.TextField(null=True)  # noqa: DJ001
    bot_refresh_token = models.TextField(null=True)  # noqa: DJ001
    bot_token_expires_at = models.DateTimeField(null=True)
    bot_id = models.CharField(null=True, max_length=32)  # noqa: DJ001
    bot_user_id = models.CharField(null=True, max_length=32)  # noqa: DJ001
    bot_scopes = models.TextField(null=True)  # noqa: DJ001
    is_enterprise_install = models.BooleanField(null=True)
    installed_at = models.DateTimeField(null=False)

    class Meta:
        indexes = [
            models.Index(fields=["client_id", "enterprise_id", "slack_team_id", "installed_at"]),
        ]


class SlackInstallation(BaseModel):
    """Copied from the example app:
    https://github.com/slackapi/bolt-python/tree/main/examples/django/oauth_app
    """

    client_id = models.CharField(null=False, max_length=32)
    app_id = models.CharField(null=False, max_length=32)
    enterprise_id = models.CharField(null=True, max_length=32)  # noqa: DJ001
    enterprise_name = models.TextField(null=True)  # noqa: DJ001
    enterprise_url = models.TextField(null=True)  # noqa: DJ001
    # renamed to avoid conflict with team_id from base model
    slack_team_id = models.CharField(null=True, max_length=32)  # noqa: DJ001
    slack_team_name = models.TextField(null=True)  # noqa: DJ001
    bot_token = models.TextField(null=True)  # noqa: DJ001
    bot_refresh_token = models.TextField(null=True)  # noqa: DJ001
    bot_token_expires_at = models.DateTimeField(null=True)
    bot_id = models.CharField(null=True, max_length=32)  # noqa: DJ001
    bot_user_id = models.TextField(null=True)  # noqa: DJ001
    bot_scopes = models.TextField(null=True)  # noqa: DJ001
    user_id = models.CharField(null=False, max_length=32)
    user_token = models.TextField(null=True)  # noqa: DJ001
    user_refresh_token = models.TextField(null=True)  # noqa: DJ001
    user_token_expires_at = models.DateTimeField(null=True)
    user_scopes = models.TextField(null=True)  # noqa: DJ001
    incoming_webhook_url = models.TextField(null=True)  # noqa: DJ001
    incoming_webhook_channel = models.TextField(null=True)  # noqa: DJ001
    incoming_webhook_channel_id = models.TextField(null=True)  # noqa: DJ001
    incoming_webhook_configuration_url = models.TextField(null=True)  # noqa: DJ001
    is_enterprise_install = models.BooleanField(null=True)
    token_type = models.CharField(null=True, max_length=32)  # noqa: DJ001
    installed_at = models.DateTimeField(null=False)

    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "client_id",
                    "enterprise_id",
                    "slack_team_id",
                    "user_id",
                    "installed_at",
                ]
            ),
        ]


class SlackOAuthState(BaseTeamModel):
    """Copied from the example app:
        https://github.com/slackapi/bolt-python/tree/main/examples/django/oauth_app

    In addition to normal uses this also serves as a mechanism for
    storing the team associated with an installation request. When the request
    callback is processed we get the team from the state and use it when creating
    the other models.
    """

    state = models.CharField(null=False, max_length=64)
    expire_at = models.DateTimeField(null=False)
    config = models.JSONField(default=dict)

    def get_request_context(self):
        """Additional context to add to the request which can be used elsewhere
        in the installation process."""
        return {"team": self.team, INSTALLATION_CONFIG: self.config}
