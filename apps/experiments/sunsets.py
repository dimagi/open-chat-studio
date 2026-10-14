"""Sunset specs for HTTP surfaces owned by this app. Imported from `ExperimentAppConfig.ready()`."""

from datetime import timedelta

from django.db.models import Exists, OuterRef, QuerySet
from django.http import HttpResponseGone
from django.utils import timezone

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.const import LEGACY_WEB_CHAT_SUCCESSOR_URL, LEGACY_WEB_CHAT_SUNSET_AT
from apps.experiments.models import Experiment, ExperimentSession
from apps.teams.flags import Flags
from apps.utils.sunset import HttpSunset, register_sunset

USAGE_WINDOW = timedelta(days=30)

LEGACY_WEB_CHAT_READ_ONLY_MESSAGE = (
    f"Invitations are being removed on {LEGACY_WEB_CHAT_SUNSET_AT:%d %b %Y} and can no longer be created or sent. "
    "Share a public link instead."
)


def _enabled_public_channels() -> QuerySet:
    return (
        ExperimentChannel.objects.filter(platform=ChannelPlatform.PUBLIC, enabled=True)
        .filter(extra_data__has_key="widget_token")
        .exclude(extra_data__widget_token="")
        .exclude(extra_data__widget_token=None)
    )


def affected_experiments() -> QuerySet:
    """Chatbots that had a web session in the usage window and have no live public link."""
    recent_web_session = ExperimentSession.objects.filter(
        experiment=OuterRef("pk"),
        experiment_channel__platform=ChannelPlatform.WEB,
        created_at__gte=timezone.now() - USAGE_WINDOW,
    )
    public_channel = _enabled_public_channels().filter(experiment=OuterRef("pk"))
    published_version = Experiment.objects.filter(working_version=OuterRef("pk"), is_default_version=True)
    return (
        Experiment.objects.working_versions_queryset()
        .filter(Exists(recent_web_session))
        .exclude(Exists(public_channel) & Exists(published_version))
    )


def live_public_channel(experiment: Experiment) -> ExperimentChannel | None:
    """The chatbot's public link channel when a visitor would reach a working chat through it."""
    channel = _enabled_public_channels().filter(experiment=experiment).first()
    if channel is None:
        return None
    if not experiment.versions.filter(is_default_version=True).exists():
        return None
    return channel


def legacy_web_chat_gone(request, team_slug, experiment_id, **kwargs) -> HttpResponseGone:
    experiment = Experiment.objects.filter(public_id=experiment_id, team=request.team).first()
    channel = live_public_channel(experiment) if experiment else None
    successor = channel.public_url if channel else LEGACY_WEB_CHAT_SUCCESSOR_URL
    return HttpResponseGone(
        f"This chat link was retired on {LEGACY_WEB_CHAT_SUNSET_AT:%d %b %Y}. Use the public link instead: {successor}"
    )


def _message(experiments: list, milestone: str) -> str:
    return (
        f"{len(experiments)} chatbot(s) on your team still receive visitors through the legacy web chat link or "
        f"invitations. The legacy web chat will be removed on {LEGACY_WEB_CHAT_SUNSET_AT:%d %b %Y}. For each "
        "chatbot: publish a version, add the Public link channel, and replace the old link with the new one. "
        "Invitations, message rating and the end-of-chat review are not carried over."
    )


LEGACY_WEB_CHAT = register_sunset(
    HttpSunset(
        key="legacy_web_chat",
        sunset_at=LEGACY_WEB_CHAT_SUNSET_AT,
        successor_url=LEGACY_WEB_CHAT_SUCCESSOR_URL,
        enforce_flag=Flags.SUNSET_LEGACY_WEB_CHAT_ENFORCE.slug,
        notification_slug="legacy-web-chat-sunset",
        notification_title="Legacy web chat is being removed",
        build_message=_message,
        affected_experiments=affected_experiments,
    )
)
