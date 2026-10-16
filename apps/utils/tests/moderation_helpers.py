DELETED_PROVIDER_NOTIFICATION = "apps.moderation.signals.moderation_provider_deleted_notification"
"""Patch target for the notification sent when a team's moderation provider is deleted."""


def select_moderation_provider(team, provider):
    """Turn moderation on for ``team`` with ``provider``."""
    team.moderation_enabled = True
    team.moderation_provider = provider
    team.save()
