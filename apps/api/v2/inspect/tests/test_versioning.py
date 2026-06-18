"""Tests for resolving ``?version=`` to a chatbot version (resolving and preloading in one query)."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.api.v2.inspect.versioning import InspectVersionError, resolve_inspect_version
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory

# Resolution embeds the inspect prefetch set, so every mode costs one resolution query plus the
# fixed prefetch fan-out: static_triggers, timeout_triggers, and the node set with each node's
# resource relations (inspect_node_queryset adds the collection_indexes M2M and
# custom_action_operations prefetches). The count is constant across modes: versioned reads join
# through ``working_version__public_id`` rather than fetching the family first, so no mode pays an
# extra resolution round trip.
EXPECTED_RESOLVE_QUERIES = 6


@pytest.mark.django_db()
def test_resolve_none_returns_working_family():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    team = family.team
    with CaptureQueriesContext(connection) as ctx:
        resolved = resolve_inspect_version(family.public_id, None, team=team)
    assert resolved == family
    assert len(ctx) == EXPECTED_RESOLVE_QUERIES


@pytest.mark.django_db()
def test_resolve_specific_version_returns_that_version():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    version = family.create_new_version()
    team = family.team
    with CaptureQueriesContext(connection) as ctx:
        resolved = resolve_inspect_version(family.public_id, str(version.version_number), team=team)
    assert resolved.id == version.id
    assert len(ctx) == EXPECTED_RESOLVE_QUERIES


@pytest.mark.django_db()
def test_resolve_default_version_returns_default_version():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    version = family.create_new_version()
    team = family.team
    with CaptureQueriesContext(connection) as ctx:
        resolved = resolve_inspect_version(family.public_id, "default", team=team)
    assert resolved.id == version.id
    assert len(ctx) == EXPECTED_RESOLVE_QUERIES


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
def test_resolved_target_has_team_loaded():
    family = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    resolved = resolve_inspect_version(family.public_id, None, team=family.team)
    # team is select_related, so accessing it costs no query
    with CaptureQueriesContext(connection) as ctx:
        _ = resolved.team.slug
    assert len(ctx) == 0
