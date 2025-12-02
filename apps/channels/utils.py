from __future__ import annotations

from urllib.parse import urlparse

from django.core.cache import cache
from django.core.validators import validate_domain_name

from apps.channels.exceptions import ExperimentChannelException
from apps.channels.models import ChannelPlatform
from apps.experiments.models import Experiment, ExperimentSession

ALL_DOMAINS = "*"
WIDGET_SESSION_CACHE_TTL = 300


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
            external_id=session_id
        )
        cache.set(cache_key, session, WIDGET_SESSION_CACHE_TTL)
        return session
    except ExperimentSession.DoesNotExist:
        return None
