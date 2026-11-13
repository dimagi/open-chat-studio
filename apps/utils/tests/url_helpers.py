"""Helpers for tests that walk a URLconf."""

from django.urls import URLPattern, URLResolver


def iter_view_callbacks(patterns, prefix=""):
    """Yield ``(route, callback)`` for every leaf pattern under ``patterns``, resolvers flattened."""
    for entry in patterns:
        if isinstance(entry, URLResolver):
            yield from iter_view_callbacks(entry.url_patterns, prefix + str(entry.pattern))
        elif isinstance(entry, URLPattern):
            yield prefix + str(entry.pattern), entry.callback
