from datetime import datetime, timedelta
from unittest import mock

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.utils import timezone

from apps.web.elevation import (
    MAX_CONCURRENT_ELEVATIONS,
    SESSION_KEY,
    Elevation,
    Grant,
    InvalidGrant,
    TooManyElevations,
    active_elevations,
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


@pytest.mark.parametrize(
    ("grant", "wire_form"),
    [
        pytest.param(Grant.DJANGO_ADMIN, "django_admin", id="django-admin"),
        pytest.param(Grant.OCS_ADMIN, "ocs_admin", id="ocs-admin"),
        pytest.param(Grant.team("acme"), "team:acme", id="team"),
    ],
)
def test_grant_round_trips_through_its_wire_form(grant, wire_form):
    assert str(grant) == wire_form
    assert Grant.parse(wire_form) == grant


@pytest.mark.parametrize(
    "wire_form",
    [
        pytest.param("", id="empty"),
        pytest.param("  ", id="blank"),
        pytest.param("team", id="team-without-slug"),
        pytest.param("team:", id="team-with-empty-slug"),
        pytest.param("django_admin:acme", id="admin-with-slug"),
        pytest.param("admin_site", id="the-old-wire-form"),
    ],
)
def test_parsing_an_unknown_grant_is_rejected(wire_form):
    with pytest.raises(InvalidGrant):
        Grant.parse(wire_form)


def test_a_team_named_after_an_admin_surface_does_not_unlock_it(request_with_session):
    """The namespaced wire form is what keeps the two apart."""
    elevation = Elevation(request_with_session)
    elevation.add(Grant.team("django_admin"))

    assert elevation.has(Grant.team("django_admin"))
    assert not elevation.has(Grant.DJANGO_ADMIN)


@pytest.mark.parametrize(
    ("grant", "is_staff", "is_superuser", "expected"),
    [
        pytest.param(Grant.DJANGO_ADMIN, True, False, True, id="django-admin-staff"),
        pytest.param(Grant.DJANGO_ADMIN, False, False, False, id="django-admin-neither"),
        pytest.param(Grant.OCS_ADMIN, True, False, True, id="ocs-admin-staff"),
        pytest.param(Grant.team("acme"), True, False, False, id="team-staff"),
        pytest.param(Grant.team("acme"), False, True, True, id="team-superuser"),
    ],
)
def test_minimum_role(grant, is_staff, is_superuser, expected):
    user = mock.Mock(is_staff=is_staff, is_superuser=is_superuser)
    assert grant.may_be_held_by(user) is expected


def test_add_grants_access(request_with_session):
    elevation = Elevation(request_with_session)
    elevation.add(Grant.team("team1"))
    assert elevation.has(Grant.team("team1"))


def test_add_does_not_duplicate_access(request_with_session):
    elevation = Elevation(request_with_session)
    elevation.add(Grant.team("team1"))
    elevation.add(Grant.team("team1"))
    assert len(request_with_session.session[SESSION_KEY]) == 1


def test_expired_access_is_not_held(request_with_session):
    elevation = Elevation(request_with_session)
    elevation.add(Grant.team("team1"))
    request_with_session.session[SESSION_KEY]["team:team1"] = int((timezone.now() - timedelta(seconds=1)).timestamp())
    assert not elevation.has(Grant.team("team1"))


def test_drop_removes_only_that_grant(request_with_session):
    elevation = Elevation(request_with_session)
    elevation.add(Grant.team("team1"))
    elevation.add(Grant.team("team2"))

    assert elevation.drop(Grant.team("team1")) is True

    assert not elevation.has(Grant.team("team1"))
    assert elevation.has(Grant.team("team2"))


def test_drop_reports_whether_the_grant_was_held(request_with_session):
    elevation = Elevation(request_with_session)
    elevation.add(Grant.team("team1"))
    assert elevation.drop(Grant.team("team1")) is True
    assert elevation.drop(Grant.team("team1")) is False
    assert elevation.drop(Grant.team("never-held")) is False


def test_expiry_prunes_only_the_expired_grant(request_with_session):
    elevation = Elevation(request_with_session)
    elevation.add(Grant.team("team1"))
    elevation.add(Grant.team("team2"))
    request_with_session.session[SESSION_KEY]["team:team1"] = int((timezone.now() - timedelta(seconds=1)).timestamp())

    assert set(elevation.active()) == {"team:team2"}


def test_active_returns_the_grant_and_its_expiry(request_with_session):
    Elevation(request_with_session).add(Grant.team("team1"))

    active = active_elevations(request_with_session)

    assert set(active) == {"team:team1"}
    assert active["team:team1"].grant == Grant.team("team1")
    assert active["team:team1"].expires_at > datetime.now()


def test_max_number_of_concurrent_elevations(request_with_session):
    elevation = Elevation(request_with_session)
    for i in range(MAX_CONCURRENT_ELEVATIONS):
        elevation.add(Grant.team(f"team{i}"))
    with pytest.raises(TooManyElevations):
        elevation.add(Grant.team("one-too-many"))


def test_active_elevations_handles_missing_user_attribute():
    request = type("DummyRequest", (), {"session": {}})()
    assert active_elevations(request) == {}


@pytest.mark.parametrize(
    "build_stored",
    [
        pytest.param(lambda expire: None, id="no-key"),
        pytest.param(lambda expire: {}, id="empty"),
        pytest.param(lambda expire: {"team:team1": expire}, id="unexpired-entry"),
    ],
)
def test_reading_access_leaves_an_unchanged_session_alone(request_with_real_session, build_stored):
    """`project_meta` runs this on every rendered page, so a write here is a write per request."""
    request = request_with_real_session
    stored = build_stored(int((timezone.now() + timedelta(seconds=60)).timestamp()))
    if stored is not None:
        request.session[SESSION_KEY] = stored
    request.session.modified = False

    active_elevations(request)

    assert request.session.modified is False


def test_reading_access_writes_once_when_pruning(request_with_real_session):
    request = request_with_real_session
    elevation = Elevation(request)
    elevation.add(Grant.team("team1"))
    elevation.add(Grant.team("team2"))
    request.session[SESSION_KEY] |= {"team:team1": int((timezone.now() - timedelta(seconds=1)).timestamp())}
    request.session.modified = False

    assert set(active_elevations(request)) == {"team:team2"}
    assert request.session.modified is True

    request.session.modified = False
    active_elevations(request)
    assert request.session.modified is False


def test_an_entry_that_no_longer_parses_is_pruned(request_with_real_session):
    """A session written by an older release carries wire forms this one cannot read."""
    request = request_with_real_session
    request.session[SESSION_KEY] = {"admin_site": int((timezone.now() + timedelta(seconds=60)).timestamp())}
    request.session.modified = False

    assert active_elevations(request) == {}
    assert request.session.modified is True
