import importlib

import pytest
from django.contrib.auth.models import Group
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from waffle.utils import get_cache

from apps.teams.models import Flag
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory

_migration = importlib.import_module("apps.teams.migrations.0016_reduce_flag_inputs_to_everyone_and_teams")
reduce_flag_inputs_to_everyone_and_teams = _migration.reduce_flag_inputs_to_everyone_and_teams


class FakeSchemaEditor:
    """Stands in for the schema editor a RunPython operation receives."""

    connection = connection


@pytest.fixture(autouse=True)
def _requires_migrations(requires_migrations):
    """Every test here loads historical state via the migration graph."""


def _run():
    """Run against the app state the migration actually receives, not the live registry."""
    state = MigrationLoader(None).project_state([("teams", "0015_team_created_by")])
    reduce_flag_inputs_to_everyone_and_teams(state.apps, FakeSchemaEditor())


def _make_flag_with_request_only_inputs(**kwargs):
    """A flag with every request-only input set, as a pre-migration row could hold."""
    flag = Flag.objects.create(
        name="flag_migration_probe",
        superusers=True,
        staff=True,
        authenticated=True,
        testing=True,
        rollout=True,
        percent=30,
        languages="en",
        **kwargs,
    )
    flag.users.add(UserFactory.create())
    flag.groups.add(Group.objects.create(name="migration-probe-group"))
    return flag


@pytest.mark.django_db()
def test_neutralises_request_only_inputs():
    """Every request-only input is reset to its inert value, M2Ms included."""
    flag = _make_flag_with_request_only_inputs()

    _run()

    flag.refresh_from_db()
    assert flag.superusers is False
    assert flag.staff is False
    assert flag.authenticated is False
    assert flag.testing is False
    assert flag.rollout is False
    assert flag.percent is None
    assert flag.languages == ""
    assert not flag.users.exists()
    assert not flag.groups.exists()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        pytest.param(False, None, id="false-becomes-none"),
        pytest.param(True, True, id="true-survives"),
        pytest.param(None, None, id="none-survives"),
    ],
)
def test_everyone_false_becomes_none(stored, expected):
    """`everyone=False` historically meant "no global override, use teams", so it becomes
    `None` before the tri-state gives `False` its hard-off meaning. Team grants survive."""
    team = TeamFactory.create()
    flag = _make_flag_with_request_only_inputs(everyone=stored)
    flag.teams.add(team)

    _run()

    flag.refresh_from_db()
    assert flag.everyone is expected
    assert list(flag.teams.all()) == [team]


@pytest.mark.django_db()
def test_flushes_every_waffle_cache_key_per_flag():
    """Waffle caches the flag instance, the all-flags list, and the user/group/team ID
    sets under separate keys; a stale copy under any of them (`superusers=True` on the
    instance, a cleared user in the ID set) would keep answering flag checks until its
    TTL, so the migration must flush them all."""
    flag = _make_flag_with_request_only_inputs()
    cache = get_cache()
    flush_keys = flag.get_flush_keys()
    for key in flush_keys:
        cache.set(key, "stale")

    _run()

    for key in flush_keys:
        assert cache.get(key) is None, f"cache key not flushed: {key}"
