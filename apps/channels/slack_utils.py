"""Shared helpers for validating Slack channel names across Slack-related forms."""


def normalize_slack_channel_name(name: str) -> str:
    """Strip a leading '#' (and surrounding whitespace) from a Slack channel name.

    Slack channel names are stored without the '#' prefix; the '@'/'#' is only a
    display convention.
    """
    name = name.strip()
    if name.startswith("#"):
        name = name[1:]
    return name


def resolve_slack_channel(provider, channel_name: str) -> dict | None:
    """Return the Slack channel dict for ``channel_name``, or None if it isn't visible to the bot."""
    service = provider.get_messaging_service()
    return service.get_channel_by_name(channel_name)
