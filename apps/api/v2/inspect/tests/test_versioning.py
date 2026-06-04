"""Tests for inspect target resolution and prefetch."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.api.v2.inspect.versioning import InspectVersionError, prefetch_inspect_target, resolve_inspect_version
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
def test_resolve_none_returns_working_family():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    team = family.team
    with CaptureQueriesContext(connection) as ctx:
        resolved = resolve_inspect_version(family.public_id, None, team=team)
    assert resolved == family
    assert len(ctx) == 1


@pytest.mark.django_db()
def test_resolve_specific_version_returns_that_version_in_one_query():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    version = family.create_new_version()
    team = family.team
    with CaptureQueriesContext(connection) as ctx:
        resolved = resolve_inspect_version(family.public_id, str(version.version_number), team=team)
    assert resolved.id == version.id
    assert len(ctx) == 1


@pytest.mark.django_db()
def test_resolve_default_version_returns_default_version_in_one_query():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    version = family.create_new_version()
    team = family.team
    with CaptureQueriesContext(connection) as ctx:
        resolved = resolve_inspect_version(family.public_id, "default", team=team)
    assert resolved.id == version.id
    assert len(ctx) == 1


@pytest.mark.django_db()
def test_resolve_unknown_version_raises_inspect_version_error():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    with pytest.raises(InspectVersionError):
        resolve_inspect_version(family.public_id, "999", team=family.team)


@pytest.mark.django_db()
def test_resolve_non_numeric_version_raises():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    with pytest.raises(InspectVersionError):
        resolve_inspect_version(family.public_id, "not-a-number", team=family.team)


@pytest.mark.django_db()
def test_resolve_other_team_public_id_raises():
    """A public_id belonging to a different team must not resolve (404 at the view)."""
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    other_team = TeamWithUsersFactory.create()
    with pytest.raises(InspectVersionError):
        resolve_inspect_version(family.public_id, None, team=other_team)


@pytest.mark.django_db()
def test_prefetch_inspect_target_returns_same_object_with_team_loaded():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    prefetched = prefetch_inspect_target(family)
    assert prefetched.id == family.id
    # team is select_related, so accessing it costs no query
    with CaptureQueriesContext(connection) as ctx:
        _ = prefetched.team.slug
    assert len(ctx) == 0
