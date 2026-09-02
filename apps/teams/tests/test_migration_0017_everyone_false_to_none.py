import importlib

import pytest
from django.contrib.auth.models import Group
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from waffle.utils import get_cache

from apps.teams.models import Flag
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory

_migration = importlib.import_module("apps.teams.migrations.0017_everyone_false_to_none")
convert_everyone_false_to_none = _migration.convert_everyone_false_to_none


class FakeSchemaEditor:
    """Stands in for the schema editor a RunPython operation receives."""

    connection = connection


@pytest.fixture(autouse=True)
def _requires_migrations(requires_migrations):
    """Every test here loads historical state via the migration graph."""


def _run():
    """Run against the app state the migration actually receives, not the live registry."""
    state = MigrationLoader(None).project_state([("teams", "0016_neutralise_request_only_flag_fields")])
    convert_everyone_false_to_none(state.apps, FakeSchemaEditor())


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
    flag = Flag.objects.create(name="flag_migration_probe", everyone=stored)
    flag.teams.add(team)

    _run()

    flag.refresh_from_db()
    assert flag.everyone is expected
    assert list(flag.teams.all()) == [team]


@pytest.mark.django_db()
def test_reneutralises_request_only_inputs():
    """A row minted between 0016 and this migration carries waffle's field defaults
    (notably `superusers=True`), so the 0016 neutralisation runs again."""
    flag = Flag.objects.create(
        name="flag_migration_probe",
        superusers=True,
        staff=True,
        authenticated=True,
        testing=True,
        rollout=True,
        percent=30,
        languages="en",
    )
    flag.users.add(UserFactory.create())
    flag.groups.add(Group.objects.create(name="migration-probe-group"))

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
def test_flushes_the_waffle_cache_per_flag():
    """Waffle caches whole Flag instances, so without a flush a stale copy with
    `everyone=False` would keep hard-switching the flag off until its TTL."""
    flag = Flag.objects.create(name="flag_migration_probe", everyone=False)
    cache = get_cache()
    cache_key = flag._cache_key(flag.name)
    cache.set(cache_key, flag)

    _run()

    assert cache.get(cache_key) is None
