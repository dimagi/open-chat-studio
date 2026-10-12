"""The progress lines ``sync_team`` prints while it runs."""

from django.core.management.color import Style


def style_synced_line(message: str, count: int, style: Style | None) -> str:
    """Colour a progress line by how much it moved: green when rows were synced, muted (ANSI faint)
    when zero so the eye skips the no-op lines. Returns the message unchanged when no style is given
    or colour is disabled (--no-color / non-tty, where the style funcs are the identity), so
    redirected logs stay clean."""
    if style is None:
        return message
    if count > 0:
        return style.SUCCESS(message)
    if style.SUCCESS("") == "":  # colour disabled -- leave it plain rather than emit raw escapes
        return message
    return f"\x1b[2m{message}\x1b[0m"
