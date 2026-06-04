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
    assert resolve_inspect_version(family, None) == family


@pytest.mark.django_db()
def test_resolve_specific_and_default():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    version = family.create_new_version()
    assert resolve_inspect_version(family, str(version.version_number)).id == version.id
    assert resolve_inspect_version(family, "default").id == version.id


@pytest.mark.django_db()
def test_resolve_unknown_version_raises_inspect_version_error():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    with pytest.raises(InspectVersionError):
        resolve_inspect_version(family, "999")


@pytest.mark.django_db()
def test_resolve_non_numeric_version_raises():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    with pytest.raises(InspectVersionError):
        resolve_inspect_version(family, "not-a-number")


@pytest.mark.django_db()
def test_prefetch_inspect_target_returns_same_object_with_team_loaded():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    prefetched = prefetch_inspect_target(family)
    assert prefetched.id == family.id
    # team is select_related, so accessing it costs no query
    with CaptureQueriesContext(connection) as ctx:
        _ = prefetched.team.slug
    assert len(ctx) == 0
