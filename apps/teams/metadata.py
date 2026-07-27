from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

FIELD_TYPES = ("text", "email", "select")


def get_team_metadata_fields() -> list[dict]:
    """Return the configured internal (staff-only) team metadata field definitions.

    Each definition is a dict with non-empty ``key`` and ``label`` entries plus a ``type``
    (one of ``text``, ``email`` or ``select``, defaulting to ``text``). ``select`` fields
    additionally carry a non-empty ``options`` list of strings. Configured via the
    ``TEAM_METADATA_FIELDS`` setting; raises ``ImproperlyConfigured`` for a malformed setting
    so the failure surfaces on first use rather than deep in a request.
    """
    raw = settings.TEAM_METADATA_FIELDS
    if not isinstance(raw, list):
        raise ImproperlyConfigured("TEAM_METADATA_FIELDS must be a list of {'key', 'label'} objects.")
    return [_validated_field(index, item) for index, item in enumerate(raw)]


def _validated_field(index: int, item) -> dict:
    if not isinstance(item, dict):
        raise ImproperlyConfigured(f"TEAM_METADATA_FIELDS[{index}] must be an object with 'key' and 'label'.")
    field = {}
    for name in ("key", "label"):
        value = item.get(name)
        if not isinstance(value, str) or not value:
            raise ImproperlyConfigured(f"TEAM_METADATA_FIELDS[{index}].{name} must be a non-empty string.")
        field[name] = value

    field_type = item.get("type", "text")
    if field_type not in FIELD_TYPES:
        raise ImproperlyConfigured(f"TEAM_METADATA_FIELDS[{index}].type must be one of {', '.join(FIELD_TYPES)}.")
    field["type"] = field_type

    if field_type == "select":
        field["options"] = _validated_options(index, item.get("options"))
    return field


def _validated_options(index: int, options) -> list[str]:
    if not isinstance(options, list) or not options:
        raise ImproperlyConfigured(f"TEAM_METADATA_FIELDS[{index}].options must be a non-empty list for select fields.")
    for option in options:
        if not isinstance(option, str) or not option:
            raise ImproperlyConfigured(f"TEAM_METADATA_FIELDS[{index}].options must contain non-empty strings.")
    return options
