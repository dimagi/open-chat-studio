from __future__ import annotations

import uuid
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.core.validators import validate_domain_name  # ty: ignore[unresolved-import]

from apps.channels.exceptions import ExperimentChannelException
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import Experiment, ExperimentSession

ALL_DOMAINS = "*"
WIDGET_SESSION_CACHE_TTL = 300
WIDGET_EMBED_KEY_CACHE_TTL = 300


def match_domain_pattern(origin_domain: str, allowed_pattern: str) -> bool:
    """Check if origin domain matches the allowed domain pattern."""
    if origin_domain == allowed_pattern:
        return True

    if allowed_pattern.startswith("*."):
        base_domain = allowed_pattern[2:]
        if origin_domain.endswith("." + base_domain):
            return True

    return False


def extract_domain_from_headers(request) -> str:
    for header in ["Origin", "Referer"]:
        if value := request.headers.get(header):
            try:
                parsed = urlparse(value)
                return parsed.hostname or ""
            except ValueError:
                pass
    return ""


def validate_domain(origin_domain: str, allowed_domains: list[str]) -> bool:
    if ALL_DOMAINS in allowed_domains:
        return True

    return any(match_domain_pattern(origin_domain, domain) for domain in allowed_domains)


def is_email_domain_allowed(email_address: str) -> bool:
    """Return True if email_address is on a domain in EMAIL_CHANNEL_ALLOWED_DOMAINS.

    Returns False for malformed addresses (no '@') and when the setting is
    empty/unset (fail-closed).
    """
    if not email_address or "@" not in email_address:
        return False
    domain = email_address.rsplit("@", 1)[1].lower()
    allowed = settings.EMAIL_CHANNEL_ALLOWED_DOMAINS
    if not allowed:
        return False
    return any(match_domain_pattern(domain, pattern.lower()) for pattern in allowed)


def get_allowed_email_domains() -> list[str]:
    """Return the configured allowed-domains list, for UI display."""
    return list(settings.EMAIL_CHANNEL_ALLOWED_DOMAINS)


def validate_platform_availability(experiment: Experiment, platform: ChannelPlatform):
    existing_platforms = {channel.platform_enum for channel in experiment.experimentchannel_set.all()}
    if platform in existing_platforms:
        raise ExperimentChannelException(f"Channel for platform '{platform.label}' already exists")

    global_platforms = ChannelPlatform.team_global_platforms()
    used_platforms = {platform for platform in existing_platforms if platform not in global_platforms}
    available_platforms = ChannelPlatform.for_dropdown(used_platforms, experiment.team)
    if not available_platforms.get(platform):
        raise ExperimentChannelException("Platform already used or not available.")


def validate_domain_or_wildcard(value):
    """Validate domain name, allowing wildcard subdomains (*.example.com)"""
    domain_part = value[2:] if value.startswith("*.") else value
    validate_domain_name(domain_part)


def _get_experiment_session_cache_key(session_id: str) -> str:
    """Generate cache key for widget session."""
    return f"WIDGET_SESSION:{session_id}"


def delete_experiment_session_cached(session_id: str) -> None:
    """Invalidate widget session cache."""
    if session_id:
        cache.delete(_get_experiment_session_cache_key(session_id))


def get_experiment_session_cached(session_id: str) -> ExperimentSession | None:
    """
    Get widget session from cache or database.

    Returns cached session if available, otherwise fetches from database
    and caches the result.
    """
    if not session_id:
        return None

    cache_key = _get_experiment_session_cache_key(session_id)

    if session := cache.get(cache_key):
        return session

    try:
        session = ExperimentSession.objects.select_related("experiment_channel", "experiment", "participant").get(
            external_id=session_id,
            experiment__is_archived=False,
        )
        cache.set(cache_key, session, WIDGET_SESSION_CACHE_TTL)
        return session
    except ExperimentSession.DoesNotExist:
        return None


def _get_widget_embed_key_cache_key(chatbot_id: str) -> str:
    return f"WIDGET_EMBED_KEY:{chatbot_id}"


def get_widget_embed_key(chatbot_id: str) -> str:
    """Cached variant of `fetch_widget_embed_key`, for the page-render path.

    The site help widget resolves its embed key on every page render, so the lookup is cached.
    Misses are cached too, so a chatbot without a widget channel doesn't cost a query per render.
    A rotated token propagates within the TTL; `clear_widget_embed_key_cache` shortens that for
    changes we know about.
    """
    if not chatbot_id:
        return ""

    cache_key = _get_widget_embed_key_cache_key(chatbot_id)
    embed_key = cache.get(cache_key)
    if embed_key is None:
        embed_key = fetch_widget_embed_key(chatbot_id)
        cache.set(cache_key, embed_key, WIDGET_EMBED_KEY_CACHE_TTL)
    return embed_key


def fetch_widget_embed_key(chatbot_id: str) -> str:
    """Return the embed key of the chatbot's embedded widget channel, or "" if it has none.

    `chatbot_id` is an experiment's `public_id`; anything that isn't a UUID has no channel.
    """
    try:
        uuid.UUID(str(chatbot_id))
    except (ValueError, TypeError):
        return ""

    widget_token = (
        ExperimentChannel.objects.filter(experiment__public_id=chatbot_id, platform=ChannelPlatform.EMBEDDED_WIDGET)
        .values_list("extra_data__widget_token", flat=True)
        .first()
    )
    return widget_token or ""


def clear_widget_embed_key_cache(chatbot_id: str) -> None:
    if chatbot_id:
        cache.delete(_get_widget_embed_key_cache_key(chatbot_id))
