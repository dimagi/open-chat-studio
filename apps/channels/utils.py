from apps.channels.models import ChannelPlatform, ExperimentChannel


def validate_embedded_widget_request(token: str, origin_domain: str, team) -> tuple[bool, ExperimentChannel]:
    if not token or not origin_domain:
        return False, None

    try:
        channel = ExperimentChannel.objects.get(
            team=team, platform=ChannelPlatform.EMBEDDED_WIDGET, extra_data__widget_token=token, deleted=False
        )

        allowed_domains = channel.extra_data.get("allowed_domains", [])

        for allowed_domain in allowed_domains:
            if match_domain_pattern(origin_domain, allowed_domain):
                return True, channel

        return False, None

    except ExperimentChannel.DoesNotExist:
        return False, None


def match_domain_pattern(origin_domain: str, allowed_pattern: str) -> bool:
    """
    Check if origin domain matches the allowed domain pattern.
    """
    if origin_domain == allowed_pattern:
        return True

    origin_parts = origin_domain.split(":")
    pattern_parts = allowed_pattern.split(":")

    origin_domain_part = origin_parts[0]
    origin_port = origin_parts[1] if len(origin_parts) > 1 else None

    pattern_domain_part = pattern_parts[0]
    pattern_port = pattern_parts[1] if len(pattern_parts) > 1 else None

    # If pattern specifies a port, origin must match that port exactly
    if pattern_port is not None:
        if origin_port != pattern_port:
            return False

    if origin_domain_part == pattern_domain_part:
        return True

    if pattern_domain_part.startswith("*."):
        base_domain = pattern_domain_part[2:]  # Remove "*."
        if origin_domain_part.endswith("." + base_domain):
            return True

    return False


def extract_domain_from_headers(request) -> str:
    origin = request.headers.get("Origin")
    if origin:
        return origin.replace("http://", "").replace("https://", "")

    referer = request.headers.get("Referer")
    if referer:
        domain = referer.replace("http://", "").replace("https://", "").split("/")[0]
        return domain

    return ""


def validate_embed_key_for_experiment(
    token: str, origin_domain: str, experiment_id: str
) -> tuple[bool, ExperimentChannel]:
    """
    Validate embedded widget request for a specific experiment.
    Used in start_session when we have experiment_id but not team yet.
    """
    if not token or not origin_domain:
        return False, None

    try:
        channel = ExperimentChannel.objects.select_related("experiment", "team").get(
            experiment__public_id=experiment_id,
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            extra_data__widget_token=token,
            deleted=False,
        )
        allowed_domains = channel.extra_data.get("allowed_domains", [])
        if not allowed_domains:
            return False, None

        for allowed_domain in allowed_domains:
            if match_domain_pattern(origin_domain, allowed_domain):
                return True, channel
        return False, None
    except ExperimentChannel.DoesNotExist:
        return False, None
