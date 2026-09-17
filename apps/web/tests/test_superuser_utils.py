from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.utils import timezone

from apps.web.superuser_utils import (
    TooManyElevatedPrivileges,
    apply_temporary_superuser_access,
    get_temporary_superuser_access,
    has_temporary_superuser_access,
    remove_expired_temporary_superuser_access,
    remove_temporary_superuser_access,
)


@pytest.fixture()
def request_with_session(rf):
    request = rf.get("/")
    request.session = {}
    request.user = mock.Mock(email="test@example.com", is_anonymous=False)
    return request


@pytest.fixture()
def request_with_real_session(rf):
    """A request carrying a session that tracks `modified`, which a plain dict does not."""
    request = rf.get("/")
    request.session = SessionStore()
    request.user = mock.Mock(email="test@example.com", is_anonymous=False)
    return request


def test_apply_temporary_superuser_access_grants_access(request_with_session):
    slug = "team1"
    apply_temporary_superuser_access(request_with_session, slug)
    assert has_temporary_superuser_access(request_with_session, slug)


def test_apply_temporary_superuser_access_does_not_duplicate_access(request_with_session):
    request = request_with_session
    slug = "team1"
    apply_temporary_superuser_access(request, slug)
    apply_temporary_superuser_access(request, slug)
    assert len(request.session["elevated_privileges"]) == 1


def test_has_temporary_superuser_access_returns_false_for_expired_access(request_with_session):
    request = request_with_session
    slug = "team1"
    apply_temporary_superuser_access(request, slug)
    request.session["elevated_privileges"][0] = (slug, int((timezone.now() - timedelta(seconds=1)).timestamp()))
    assert not has_temporary_superuser_access(request, slug)


def test_remove_temporary_superuser_access_removes_access(request_with_session):
    request = request_with_session
    slug = "team1"
    apply_temporary_superuser_access(request, slug)
    remove_temporary_superuser_access(request, slug)
    assert not has_temporary_superuser_access(request, slug)


def test_remove_expired_temporary_superuser_access_removes_only_expired_access(request_with_session):
    request = request_with_session
    slug1 = "team1"
    slug2 = "team2"
    apply_temporary_superuser_access(request, slug1)
    apply_temporary_superuser_access(request, slug2)
    request.session["elevated_privileges"][0] = (slug1, int((timezone.now() - timedelta(seconds=1)).timestamp()))
    remove_expired_temporary_superuser_access(request)
    assert not has_temporary_superuser_access(request, slug1)
    assert has_temporary_superuser_access(request, slug2)


def test_get_temporary_superuser_access_returns_correct_access(request_with_session):
    request = request_with_session
    slug = "team1"
    apply_temporary_superuser_access(request, slug)
    access = get_temporary_superuser_access(request)
    assert slug in access


def test_max_number_of_concurrent_privileges(request_with_session):
    request = request_with_session
    for i in range(5):
        apply_temporary_superuser_access(request, f"team{i}")
    with pytest.raises(TooManyElevatedPrivileges):
        apply_temporary_superuser_access(request, "team6")


def test_invalid_slug(request_with_session):
    with pytest.raises(ValueError, match="Invalid grant"):
        apply_temporary_superuser_access(request_with_session, "  ")


def test_get_temporary_superuser_access_handles_missing_user_attribute():
    request = type("DummyRequest", (), {"session": {}})()
    result = get_temporary_superuser_access(request)
    assert result == {}


def test_remove_temporary_superuser_access_reports_whether_the_grant_was_held(request_with_session):
    request = request_with_session
    apply_temporary_superuser_access(request, "team1")
    assert remove_temporary_superuser_access(request, "team1") is True
    assert remove_temporary_superuser_access(request, "team1") is False
    assert remove_temporary_superuser_access(request, "never-held") is False


@pytest.mark.parametrize(
    "build_stored",
    [
        pytest.param(lambda expire: None, id="no-key"),
        pytest.param(lambda expire: [], id="empty"),
        pytest.param(lambda expire: [("team1", expire)], id="tuple-entries"),
        pytest.param(lambda expire: [["team1", expire]], id="list-entries-as-json-returns-them"),
    ],
)
def test_reading_access_leaves_an_unchanged_session_alone(request_with_real_session, build_stored):
    """`project_meta` runs this on every rendered page, so a write here is a write per request."""
    request = request_with_real_session
    stored = build_stored(int((timezone.now() + timedelta(seconds=60)).timestamp()))
    if stored is not None:
        request.session["elevated_privileges"] = stored
    request.session.modified = False

    get_temporary_superuser_access(request)

    assert request.session.modified is False


def test_reading_access_writes_once_when_pruning(request_with_real_session):
    request = request_with_real_session
    apply_temporary_superuser_access(request, "team1")
    apply_temporary_superuser_access(request, "team2")
    request.session["elevated_privileges"] = [
        ("team1", int((timezone.now() - timedelta(seconds=1)).timestamp())),
        request.session["elevated_privileges"][1],
    ]
    request.session.modified = False

    assert set(get_temporary_superuser_access(request)) == {"team2"}
    assert request.session.modified is True

    request.session.modified = False
    get_temporary_superuser_access(request)
    assert request.session.modified is False
