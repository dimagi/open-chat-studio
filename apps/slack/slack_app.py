from django.conf import settings
from django.contrib.messages import constants
from django.urls import reverse
from django.utils.functional import SimpleLazyObject
from slack_bolt import App, BoltRequest, BoltResponse
from slack_bolt.adapter.django import SlackRequestHandler
from slack_bolt.logger import get_bolt_logger
from slack_bolt.oauth import OAuthFlow
from slack_bolt.oauth.callback_options import CallbackOptions, FailureArgs, SuccessArgs
from slack_bolt.oauth.internals import build_detailed_error
from slack_bolt.oauth.oauth_settings import OAuthSettings
from slack_bolt.util.utils import create_web_client
from slack_sdk.oauth import OAuthStateUtils
from slack_sdk.oauth.installation_store import Installation

from apps.service_providers.const import MESSAGING
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.web.meta import absolute_url

from . import const
from .const import INSTALLATION_CONFIG
from .slack_datastores import DjangoInstallationStore, DjangoOAuthStateStore
from .slack_listeners import load_installation, new_message

bolt_logger = get_bolt_logger(App)


def get_slack_app():
    client_id, client_secret, signing_secret, scopes = (
        settings.SLACK_CLIENT_ID,
        settings.SLACK_CLIENT_SECRET,
        settings.SLACK_SIGNING_SECRET,
        settings.SLACK_SCOPES,
    )

    app = App(
        signing_secret=signing_secret,
        logger=bolt_logger,
        oauth_flow=CustomOauthFlow(
            client=create_web_client(logger=bolt_logger),
            logger=bolt_logger,
            settings=OAuthSettings(
                client_id=client_id,
                client_secret=client_secret,
                scopes=scopes,
                user_scopes=[],
                redirect_uri=get_redirect_uri(),
                install_page_rendering_enabled=False,
                # If you want to test token rotation, enabling the following line will make it easy
                # token_rotation_expiration_minutes=1000000,
                installation_store=DjangoInstallationStore(
                    client_id=client_id,
                    logger=bolt_logger,
                ),
                state_store=DjangoOAuthStateStore(
                    expiration_seconds=120,
                    logger=bolt_logger,
                ),
                callback_options=CallbackOptions(success=_handle_success, failure=_handle_failure),
            ),
        ),
    )
    app.use(load_installation)
    app.event({"type": "message"})(new_message)
    return app


app: App = SimpleLazyObject(get_slack_app)  # ty: ignore[invalid-assignment]
handler = SimpleLazyObject(lambda: SlackRequestHandler(app=app))


class CustomOauthFlow(OAuthFlow):
    def store_installation(self, request: BoltRequest, installation: Installation):
        """Overridden so that we can add the team to the installation data. This
        gets passed through to the Django model in the data store."""
        for key, value in request.context[INSTALLATION_CONFIG].items():
            installation.set_custom_value(key, value)
        super().store_installation(request, installation)

    def issue_new_state(self, request: BoltRequest) -> str:
        """Overridden to allow passing the team to the State Store"""
        return self.settings.state_store.issue(request.context["team"], request.context[INSTALLATION_CONFIG])


def get_redirect_uri():
    path = get_redirect_uri_path()
    return absolute_url(path, is_secure=True)  # this always has to be secure


def get_redirect_uri_path():
    return reverse("slack_global:oauth_redirect")


def _handle_success(args: SuccessArgs) -> BoltResponse:
    request = args.request
    bolt_logger.debug(f"Handling an OAuth callback success (request: {request.query})")
    team = request.context["team"]
    installation = args.installation
    installation_id = installation.get_custom_value(const.DJANGO_ID)

    for provider in MessagingProvider.objects.filter(team=team, type=MessagingProviderType.slack):
        if provider.config.get("slack_installation_id") == installation_id:
            redirect_to = reverse("service_providers:edit", args=[team.slug, MESSAGING, provider.id])
            break
    else:
        provider = MessagingProvider.objects.create(
            team=team,
            type=MessagingProviderType.slack,
            name=f"{installation.team_name}",
            config={
                "slack_team_id": installation.team_id,
                "slack_installation_id": installation_id,
            },
        )
        redirect_to = reverse("service_providers:edit", args=[team.slug, MESSAGING, provider.id])

    response = BoltResponse(
        status=302,
        headers={
            "Set-Cookie": OAuthStateUtils().build_set_cookie_for_deletion(),
            "Location": redirect_to,
        },
    )
    response._messages = [
        (
            constants.SUCCESS,
            f'Slack Workspace "{args.installation.team_name}" successfully connected. '
            f"You can now chat with the bot by DM-ing {settings.SLACK_BOT_NAME} in your workspace. ",
        )
    ]
    return response


def _handle_failure(args: FailureArgs) -> BoltResponse:
    request = args.request
    team = request.context["team"]
    bolt_logger.debug(
        f"Handling an OAuth callback failure (reason: {args.reason}, error: {args.error}, request: {request.query})"
    )
    response = BoltResponse(
        status=302,
        headers={
            "Set-Cookie": OAuthStateUtils().build_set_cookie_for_deletion(),
            "Location": reverse("service_providers:new", args=[team.slug, MESSAGING]),
        },
    )
    reason = _get_detailed_error(args.reason)
    response._messages = [(constants.ERROR, f"Error connecting Slack Workspace: {reason}")]
    return response


def _get_detailed_error(reason):
    """Translate errors to more meaningful values"""
    if reason == "access_denied":
        return "Access denied. Did you cancel the installation?"
    else:
        return build_detailed_error(reason)
