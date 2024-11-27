from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from apps.web.superuser_utils import (
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
    request.user = mock.Mock(email="test@example.com")
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
    with pytest.raises(ValueError, match="Maximum number of concurrent privileges exceeded"):
        apply_temporary_superuser_access(request, "team6")


def test_invalid_slug(request_with_session):
    with pytest.raises(ValueError, match="Invalid grant"):
        apply_temporary_superuser_access(request_with_session, "  ")
