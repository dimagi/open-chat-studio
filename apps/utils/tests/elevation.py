from django.utils import timezone

from apps.web.elevation import EXPIRY, SESSION_KEY, Grant


def elevate_session(client, grant: Grant) -> None:
    """Put `grant` in the test client's session, standing in for the re-authentication round trip.

    The round trip itself is covered by `apps/web/tests/test_elevation_views.py`; tests of the
    views behind an elevation only need the grant held.
    """
    session = client.session
    held = session.get(SESSION_KEY) or {}
    session[SESSION_KEY] = held | {str(grant): int(timezone.now().timestamp()) + EXPIRY}
    session.save()
