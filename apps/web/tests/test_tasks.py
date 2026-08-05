from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session
from django.utils import timezone

from apps.web.tasks import clear_expired_sessions


def _create_session(session_key: str, expire_date):
    return Session.objects.create(session_key=session_key, session_data="", expire_date=expire_date)


@pytest.mark.django_db()
def test_clear_expired_sessions_deletes_only_expired():
    now = timezone.now()
    expired = _create_session("expired", now - timedelta(days=1))
    live = _create_session("live", now + timedelta(days=1))

    clear_expired_sessions()

    assert not Session.objects.filter(session_key=expired.session_key).exists()
    assert Session.objects.filter(session_key=live.session_key).exists()


@pytest.mark.django_db()
def test_clear_expired_sessions_skips_session_renewed_after_selection(monkeypatch):
    """A session renewed between the select and the delete must survive."""
    now = timezone.now()
    renewed = _create_session("renewed", now - timedelta(days=1))

    real_filter = Session.objects.filter

    def filter_then_renew(*args, **kwargs):
        qs = real_filter(*args, **kwargs)
        if "session_key__in" in kwargs:
            # Simulate a request renewing the session between the select and this delete.
            real_filter(session_key=renewed.session_key).update(expire_date=now + timedelta(days=1))
        return qs

    monkeypatch.setattr(Session.objects, "filter", filter_then_renew)

    clear_expired_sessions()

    assert Session.objects.filter(session_key=renewed.session_key).exists()


@pytest.mark.django_db()
def test_clear_expired_sessions_works_across_batches(monkeypatch):
    """The batching loop must drain the backlog, not just the first chunk."""
    monkeypatch.setattr("apps.web.tasks.SESSION_CLEANUP_BATCH_SIZE", 2)
    expired_at = timezone.now() - timedelta(days=1)
    for i in range(5):
        _create_session(f"expired-{i}", expired_at)

    clear_expired_sessions()

    assert not Session.objects.exists()
