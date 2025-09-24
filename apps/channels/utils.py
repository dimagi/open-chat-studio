from urllib.parse import urlparse

from django.core.validators import validate_domain_name

from apps.channels.exceptions import ExperimentChannelException
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import Experiment


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
        value = request.headers.get(header)
        if value:
            parsed = urlparse(value)
            return parsed.hostname or ""
    return ""


def validate_embed_key_for_experiment(token: str, origin_domain: str, experiment_id: str) -> ExperimentChannel | None:
    """
    Validate embedded widget request for a specific experiment.
    Used in start_session when we have experiment_id but not team yet.

    Returns:
        ExperimentChannel if validation succeeds, None otherwise.
    """
    if not token or not origin_domain:
        return None

    try:
        channel = ExperimentChannel.objects.select_related("experiment", "team").get(
            experiment__public_id=experiment_id,
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            extra_data__widget_token=token,
            deleted=False,
        )
        allowed_domains = channel.extra_data.get("allowed_domains", [])
        if not allowed_domains:
            return None

        for allowed_domain in allowed_domains:
            if match_domain_pattern(origin_domain, allowed_domain):
                return channel

        return None

    except ExperimentChannel.DoesNotExist:
        return None


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
