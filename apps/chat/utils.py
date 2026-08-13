from urllib.parse import urlparse

SAFE_LINK_SCHEMES = frozenset({"http", "https"})


def safe_link_url(value) -> str | None:
    """Return ``value`` if it is safe to use as a link target, otherwise ``None``.

    Only absolute ``http``/``https`` URLs are considered safe. Values that come from
    untrusted sources (e.g. the ``Referer`` header) must be passed through this before
    being stored or rendered into an ``href``: template autoescaping prevents attribute
    breakout but does nothing about the URL scheme, so a ``javascript:`` URI would
    execute in our own origin when a user clicks the link.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        # malformed input (e.g. an unterminated IPv6 host) is not a usable link
        return None
    if parsed.scheme.lower() not in SAFE_LINK_SCHEMES or not parsed.netloc:
        return None
    return value
