from slack_bolt import BoltContext
from slack_sdk import WebClient
from slack_sdk.http_retry import RateLimitErrorRetryHandler

from apps.slack.models import SlackInstallation
from apps.slack.slack_app import app


def get_slack_client(installation_id: int, do_retries: bool = False) -> WebClient:
    installation = SlackInstallation.objects.get(id=installation_id)
    if app._authorize is None:
        raise Exception("Slack app authorization is not configured")
    # this handles token expiration and rotation
    auth_result = app._authorize(
        context=BoltContext({"client": app.client, "is_enterprise_install": installation.is_enterprise_install}),
        enterprise_id=installation.enterprise_id,
        team_id=installation.slack_team_id,
        user_id=installation.user_id,
    )
    if auth_result is None:
        raise Exception("Unable to authenticate")

    token = auth_result.bot_token or auth_result.user_token
    return WebClient(
        token=token,
        base_url=app.client.base_url,
        timeout=app.client.timeout,
        ssl=app.client.ssl,
        proxy=app.client.proxy,
        headers=app.client.headers,
        team_id=installation.slack_team_id,
        retry_handlers=[RateLimitErrorRetryHandler()] if do_retries else None,
    )
