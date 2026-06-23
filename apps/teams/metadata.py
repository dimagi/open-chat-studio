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

    fields = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ImproperlyConfigured(f"TEAM_METADATA_FIELDS[{index}] must be an object with 'key' and 'label'.")
        key, label = item.get("key"), item.get("label")
        if not (isinstance(key, str) and key and isinstance(label, str) and label):
            raise ImproperlyConfigured(f"TEAM_METADATA_FIELDS[{index}] 'key' and 'label' must be non-empty strings.")
        fields.append({"key": key, "label": label})
    return fields
