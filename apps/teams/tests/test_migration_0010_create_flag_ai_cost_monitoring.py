import importlib

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from field_audit.models import AuditAction

from apps.teams.models import Flag

_migration = importlib.import_module("apps.teams.migrations.0010_create_flag_ai_cost_monitoring")
forwards = _migration.forwards
FLAG_NAME = _migration.FLAG_NAME


class FakeSchemaEditor:
    """Stands in for the schema editor a RunPython operation receives."""

    connection = connection


@pytest.fixture(autouse=True)
def _requires_migrations(requires_migrations):
    """Every test here loads historical state via the migration graph."""


def _run():
    """Run against the app state the migration actually receives, not the live registry."""
    state = MigrationLoader(None).project_state([("teams", "0009_merge_pipeline_admin_into_experiment_admin")])
    forwards(state.apps, FakeSchemaEditor())


@pytest.mark.django_db()
def test_pre_created_flag_carries_no_global_override(request):
    """The pre-created row must be off by absence, not by a stored `everyone=False`:
    this operation is not elidable, so a squash that drops 0016 and 0017 would leave a
    hard off no team grant could turn on."""
    Flag.objects.filter(name=FLAG_NAME).delete(audit_action=AuditAction.IGNORE)
    request.addfinalizer(Flag(name=FLAG_NAME).flush)

    _run()

    flag = Flag.objects.get(name=FLAG_NAME)
    assert flag.everyone is None
    assert flag.superusers is False
