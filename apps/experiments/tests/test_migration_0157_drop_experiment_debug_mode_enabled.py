import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

STATE_REMOVED = ("experiments", "0156_remove_experiment_debug_mode_enabled_state")
COLUMN_DROPPED = ("experiments", "0157_drop_experiment_debug_mode_enabled")


@pytest.fixture(autouse=True)
def _requires_migrations(requires_migrations):
    """Every test here moves the database along the migration graph."""


def _migrate(target):
    executor = MigrationExecutor(connection)
    executor.migrate([target])


def _debug_mode_column():
    with connection.cursor() as cursor:
        columns = connection.introspection.get_table_description(cursor, "experiments_experiment")
    return next((column for column in columns if column.name == "debug_mode_enabled"), None)


@pytest.mark.django_db()
def test_drops_the_column():
    _migrate(STATE_REMOVED)
    assert _debug_mode_column() is not None

    _migrate(COLUMN_DROPPED)

    assert _debug_mode_column() is None


@pytest.mark.django_db()
def test_reversing_restores_the_column_with_its_default():
    _migrate(STATE_REMOVED)

    column = _debug_mode_column()
    assert column is not None
    assert not column.null_ok
    assert column.default == "false"
