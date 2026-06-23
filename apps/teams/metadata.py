from django.conf import settings


def get_team_metadata_fields() -> list[dict]:
    """Return the configured internal (staff-only) team metadata field definitions.

    Each definition is a dict with ``key`` and ``label`` entries, configured via the
    ``TEAM_METADATA_FIELDS`` setting.
    """
    return settings.TEAM_METADATA_FIELDS
