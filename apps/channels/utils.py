from urllib.parse import urlparse

from django.core.validators import validate_domain_name

from apps.channels.exceptions import ExperimentChannelException
from apps.channels.models import ChannelPlatform
from apps.experiments.models import Experiment

ALL_DOMAINS = "*"


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

    for domain in allowed_domains:
        if match_domain_pattern(origin_domain, domain):
            return True

    return False


def validate_platform_availability(experiment: Experiment, platform: ChannelPlatform):
    existing_platforms = {
        channel.platform_enum for channel in experiment.experimentchannel_set.all()
    }
    if platform in existing_platforms:
        raise ExperimentChannelException(
            f"Channel for platform '{platform.label}' already exists"
        )

    global_platforms = ChannelPlatform.team_global_platforms()
    used_platforms = {
        platform for platform in existing_platforms if platform not in global_platforms
    }
    available_platforms = ChannelPlatform.for_dropdown(used_platforms, experiment.team)
    if not available_platforms.get(platform):
        raise ExperimentChannelException("Platform already used or not available.")


def validate_domain_or_wildcard(value):
    """Validate domain name, allowing wildcard subdomains (*.example.com)"""
    domain_part = value[2:] if value.startswith("*.") else value
    validate_domain_name(domain_part)
