"""The public link page: the chat widget in kiosk mode, on the OCS host, for one chatbot.

The page never creates a session. The widget starts its own through the Chat API with the
channel's embed key, and the API enforces every refusal the page shows as a banner.
"""

import json
from dataclasses import dataclass

from django.http import Http404
from django.template.response import TemplateResponse

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chatbots.version_resolver import NoPublishedVersion, VersionSelectionRule, resolve_chatbot_version
from apps.experiments.models import Experiment
from apps.experiments.rate_limit_keys import public_chat_rate_limited
from apps.web.meta import canonical_hostname, get_server_root, hostname_of
from apps.web.waf import WafRule, waf_allow

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://unpkg.com; "
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
    "font-src 'self' data: https://cdnjs.cloudflare.com; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "object-src 'none'"
)


class PublicLinkWrongHost(Http404):
    """'Tagged' 404 that lets 404.html name the host public links are served from.

    A link tried on the wrong host is indistinguishable from a mistyped token without it.
    Only a signed-in viewer is told the difference. See 404.html.
    """


@dataclass(frozen=True)
class PageState:
    code: str
    banner: str | None

    @property
    def live(self) -> bool:
        return self.banner is None


def _page_state(channel: ExperimentChannel) -> tuple[PageState, Experiment | None]:
    """The banner a visitor sees, and the version whose name and description the page shows.

    The published version is resolved before any refusal so that every state names the chatbot
    the visitor could have reached. A draft that has since been renamed stays internal.
    """
    try:
        published = resolve_chatbot_version(channel.experiment, VersionSelectionRule.LATEST_PUBLISHED)
    except NoPublishedVersion:
        published = None
    if channel.is_disabled:
        return PageState("disabled", channel.disabled_message or "This chatbot is temporarily unavailable."), published
    if published is None:
        return PageState("no_published_version", "This chatbot is not published yet."), None
    if published.consent_form_id:
        banner = "This chatbot needs your consent, which the public link cannot collect yet."
        return PageState("consent_unavailable", banner), published
    return PageState("live", None), published


@waf_allow(WafRule.NoUserAgent_HEADER)
@public_chat_rate_limited
def public_link_page(request, token: str):
    if hostname_of(request.get_host()) != canonical_hostname():
        raise PublicLinkWrongHost() if request.user.is_authenticated else Http404()
    channel = (
        ExperimentChannel.objects.select_related("experiment", "team")
        .filter(platform=ChannelPlatform.PUBLIC, extra_data__widget_token=token)
        .first()
    )
    if channel is None:
        raise Http404()

    state, published = _page_state(channel)
    shown = published or channel.experiment
    member = request.user.is_authenticated and channel.team.members.filter(id=request.user.id).exists()
    response = TemplateResponse(
        request,
        "chatbots/public_link.html",
        {
            "channel": channel,
            "state": state,
            "chatbot_name": shown.name,
            "chatbot_description": shown.description,
            "public_id": channel.experiment.public_id,
            "token": token,
            "api_base_url": get_server_root(),
            "welcome_json": json.dumps(channel.extra_data.get("welcome_messages", [])),
            "starters_json": json.dumps(channel.extra_data.get("starter_questions", [])),
            "user": request.user if member else None,
            "widget_enabled": state.live or (member and state.code != "disabled"),
        },
    )
    response["X-Robots-Tag"] = "noindex"
    response["Referrer-Policy"] = "origin"
    response["Content-Security-Policy"] = CSP
    return response
