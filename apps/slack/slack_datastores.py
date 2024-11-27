from logging import Logger
from uuid import uuid4

from django.utils import timezone
from django.utils.timezone import is_naive, make_aware
from slack_sdk.oauth import InstallationStore, OAuthStateStore
from slack_sdk.oauth.installation_store import Bot, Installation

from . import const
from .models import SlackBot, SlackInstallation, SlackOAuthState


class DjangoInstallationStore(InstallationStore):
    """
    Django adapter for Bolt Installation store. Responsible for saving and retrieving
    installation data.

    Mostly copied from the example app:
    https://github.com/slackapi/bolt-python/tree/main/examples/django/oauth_app
    """

    client_id: str

    def __init__(
        self,
        client_id: str,
        logger: Logger,
    ):
        self.client_id = client_id
        self._logger = logger

    @property
    def logger(self) -> Logger:
        return self._logger

    def save(self, installation: Installation):
        installation_data = rename_fields(installation.to_dict())
        make_timestamps_aware(installation_data)

        installation_data["client_id"] = self.client_id
        base_qs = (
            SlackInstallation.objects.filter(client_id=self.client_id)
            .filter(enterprise_id=installation.enterprise_id)
            .filter(slack_team_id=installation.team_id)
        )
        row_to_update = base_qs.first()
        if row_to_update is not None:
            for key, value in installation_data.items():
                setattr(row_to_update, key, value)
            row_to_update.save()
            installation.set_custom_value(const.DJANGO_ID, row_to_update.id)
        else:
            slack_installation = SlackInstallation(**remove_nulls(installation_data))
            slack_installation.save()
            installation.set_custom_value(const.DJANGO_ID, slack_installation.id)

        self.save_bot(installation.to_bot())

    def save_bot(self, bot: Bot):
        data = bot.to_dict()
        data.pop(const.DJANGO_ID, None)
        bot_data = rename_fields(data)
        make_timestamps_aware(bot_data)

        bot_data["client_id"] = self.client_id

        row_to_update = (
            SlackBot.objects.filter(client_id=self.client_id)
            .filter(enterprise_id=bot.enterprise_id)
            .filter(slack_team_id=bot.team_id)
            .first()
        )
        if row_to_update is not None:
            for key, value in bot_data.items():
                setattr(row_to_update, key, value)
            row_to_update.save()
        else:
            SlackBot(**remove_nulls(bot_data)).save()

    def find_bot(
        self,
        *,
        enterprise_id: str | None,
        team_id: str | None,
        is_enterprise_install: bool | None = False,
    ) -> Bot | None:
        e_id = enterprise_id or None
        t_id = team_id or None
        if is_enterprise_install:
            t_id = None
        bot = (
            SlackBot.objects.filter(client_id=self.client_id)
            .filter(enterprise_id=e_id)
            .filter(slack_team_id=t_id)
            .order_by("-installed_at")
            .first()
        )
        if bot:
            return Bot(
                app_id=bot.app_id,
                enterprise_id=bot.enterprise_id,
                team_id=bot.slack_team_id,
                bot_token=bot.bot_token,
                bot_refresh_token=bot.bot_refresh_token,
                bot_token_expires_at=bot.bot_token_expires_at,
                bot_id=bot.bot_id,
                bot_user_id=bot.bot_user_id,
                bot_scopes=bot.bot_scopes,
                installed_at=bot.installed_at,
            )
        return None

    def find_installation(
        self,
        *,
        enterprise_id: str | None,
        team_id: str | None,
        user_id: str | None = None,
        is_enterprise_install: bool | None = False,
    ) -> Installation | None:
        e_id = enterprise_id or None
        t_id = team_id or None
        if is_enterprise_install:
            t_id = None

        query = (
            SlackInstallation.objects.filter(client_id=self.client_id)
            .filter(enterprise_id=e_id)
            .filter(slack_team_id=t_id)
        )
        if user_id is not None:
            query = query.filter(user_id=user_id)

        installation = query.order_by("-installed_at").first()
        if installation:
            if user_id is not None:
                # Fetch the latest bot token
                bot = (
                    SlackInstallation.objects.filter(client_id=self.client_id)
                    .exclude(bot_token__isnull=True)
                    .filter(enterprise_id=e_id)
                    .filter(slack_team_id=t_id)
                    .order_by("-installed_at")
                    .first()
                )
                if bot:
                    installation.bot_id = bot.bot_id
                    installation.bot_user_id = bot.bot_user_id
                    installation.bot_scopes = bot.bot_scopes
                    installation.bot_token = bot.bot_token
                    installation.bot_refresh_token = bot.bot_refresh_token
                    installation.bot_token_expires_at = bot.bot_token_expires_at

            return Installation(
                app_id=installation.app_id,
                enterprise_id=installation.enterprise_id,
                team_id=installation.slack_team_id,
                bot_token=installation.bot_token,
                bot_refresh_token=installation.bot_refresh_token,
                bot_token_expires_at=installation.bot_token_expires_at,
                bot_id=installation.bot_id,
                bot_user_id=installation.bot_user_id,
                bot_scopes=installation.bot_scopes,
                user_id=installation.user_id,
                user_token=installation.user_token,
                user_refresh_token=installation.user_refresh_token,
                user_token_expires_at=installation.user_token_expires_at,
                user_scopes=installation.user_scopes,
                incoming_webhook_url=installation.incoming_webhook_url,
                incoming_webhook_channel_id=installation.incoming_webhook_channel_id,
                incoming_webhook_configuration_url=installation.incoming_webhook_configuration_url,
                installed_at=installation.installed_at,
            )
        return None


class DjangoOAuthStateStore(OAuthStateStore):
    """
    Adapter for storing state during app installation flow.

    Mostly copied from the example app:
    https://github.com/slackapi/bolt-python/tree/main/examples/django/oauth_app
    """

    expiration_seconds: int

    def __init__(
        self,
        expiration_seconds: int,
        logger: Logger,
    ):
        self.expiration_seconds = expiration_seconds
        self._logger = logger

    @property
    def logger(self) -> Logger:
        return self._logger

    def issue(self, team, config: dict) -> str:
        state: str = str(uuid4())
        expire_at = timezone.now() + timezone.timedelta(seconds=self.expiration_seconds)
        # save the team on the state so that we can look it up during the callback request
        row = SlackOAuthState(team=team, adapter=state, expire_at=expire_at, config=config)
        row.save()
        return state

    def consume(self, state: str) -> bool:
        rows = SlackOAuthState.objects.filter(adapter=state).filter(expire_at__gte=timezone.now())
        if len(rows) > 0:
            for row in rows:
                row.delete()
            return True
        return False


def rename_fields(data):
    """Rename fields that we have changed in our data models"""
    data["slack_team_id"] = data.pop("team_id")
    data["slack_team_name"] = data.pop("team_name")
    return data


def make_timestamps_aware(data):
    if is_naive(data["installed_at"]):
        data["installed_at"] = make_aware(data["installed_at"])
    if data.get("bot_token_expires_at") is not None and is_naive(data["bot_token_expires_at"]):
        data["bot_token_expires_at"] = make_aware(data["bot_token_expires_at"])
    if data.get("user_token_expires_at") is not None and is_naive(data["user_token_expires_at"]):
        data["user_token_expires_at"] = make_aware(data["user_token_expires_at"])


def remove_nulls(data):
    return {k: v for k, v in data.items() if v is not None}
