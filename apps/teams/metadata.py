from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def get_team_metadata_fields() -> list[dict[str, str]]:
    """Return the configured internal (staff-only) team metadata field definitions.

    Each definition is a dict with non-empty ``key`` and ``label`` entries, configured via
    the ``TEAM_METADATA_FIELDS`` setting. Raises ``ImproperlyConfigured`` for a malformed
    setting so the failure surfaces on first use rather than deep in a request.
    """
    raw = settings.TEAM_METADATA_FIELDS
    if not isinstance(raw, list):
        raise ImproperlyConfigured("TEAM_METADATA_FIELDS must be a list of {'key', 'label'} objects.")
    return [_validated_field(index, item) for index, item in enumerate(raw)]


def _validated_field(index: int, item) -> dict[str, str]:
    if not isinstance(item, dict):
        raise ImproperlyConfigured(f"TEAM_METADATA_FIELDS[{index}] must be an object with 'key' and 'label'.")
    field = {}
    for name in ("key", "label"):
        value = item.get(name)
        if not isinstance(value, str) or not value:
            raise ImproperlyConfigured(f"TEAM_METADATA_FIELDS[{index}].{name} must be a non-empty string.")
        field[name] = value
    return field
