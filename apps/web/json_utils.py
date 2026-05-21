"""Shared JSON helpers used across the OCS web layer."""

from django.core.serializers.json import DjangoJSONEncoder


class BytesAwareJSONEncoder(DjangoJSONEncoder):
    """JSON encoder that gracefully serializes ``bytes`` values.

    The default ``json.JSONEncoder`` (and Django's ``DjangoJSONEncoder``) does
    not know how to serialize raw ``bytes`` objects, which can sneak into
    ``JSONField`` values (e.g. ``ParticipantData.data``) when an upstream
    integration stores a payload without decoding it. Rather than raising
    ``TypeError: Object of type bytes is not JSON serializable``, decode the
    bytes as UTF-8 with ``errors='replace'`` so the data is still displayable.
    """

    def default(self, o):
        if isinstance(o, (bytes, bytearray)):
            return bytes(o).decode("utf-8", errors="replace")
        return super().default(o)