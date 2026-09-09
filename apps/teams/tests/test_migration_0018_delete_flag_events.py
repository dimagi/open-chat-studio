import importlib

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from apps.teams.models import Flag
from apps.utils.factories.team import TeamFactory

_migration = importlib.import_module("apps.teams.migrations.0018_delete_flag_events")
delete_events_flag = _migration.delete_events_flag

FLAG_NAME = "flag_events"


class FakeSchemaEditor:
    connection = connection


@pytest.fixture(autouse=True)
def _requires_migrations(requires_migrations):
    """Every test here loads historical state via the migration graph."""


def _run():
    """Run against the app state the migration actually receives, not the live registry."""
    state = MigrationLoader(None).project_state([("teams", "0017_team_require_mfa")])
    delete_events_flag(state.apps, FakeSchemaEditor())


@pytest.mark.django_db()
def test_deletes_the_flag_and_its_team_grants():
    flag = Flag.objects.create(name=FLAG_NAME, everyone=None)
    flag.teams.add(TeamFactory.create())
    flag.flush()

    _run()

    assert not Flag.objects.filter(name=FLAG_NAME).exists()
    assert not Flag.teams.through.objects.filter(flag_id=flag.pk).exists()


@pytest.mark.django_db()
def test_leaves_no_flag_in_the_cache():
    """A cached instance would keep the flag answering checks after its row is gone."""
    flag = Flag.objects.create(name=FLAG_NAME, everyone=True)
    flag.flush()
    assert Flag.get(FLAG_NAME).pk is not None

    _run()

    assert Flag.get(FLAG_NAME).pk is None


@pytest.mark.django_db()
def test_is_a_no_op_when_the_flag_is_already_gone():
    """Self-hosters and dev DBs may have deleted the row by hand already."""
    _run()

    assert not Flag.objects.filter(name=FLAG_NAME).exists()
