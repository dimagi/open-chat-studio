import pytest
from django.core.management import call_command
from django.db import transaction
from django.db.backends.base.base import BaseDatabaseWrapper


def django_db_with_data():
    """Shortcut decorator for tests that need both a live DB server (TransactionTestCase) and migration data.

    Use this for tests that use the `live_server` fixture or otherwise require genuine transaction-mode
    testing (e.g. testing on_commit hooks). These tests are typically marked @pytest.mark.integration.

    See also `apps.conftest._django_db_restore_serialized`.
    """

    def _inner(func):
        return pytest.mark.django_db(
            serialized_rollback=True,  # restore serialized DB state after the transaction flush
            transaction=True,  # required for serialized_rollback to work; also needed for live_server
        )(func)

    return _inner


def django_db_transactional():
    """Shortcut decorator for tests that genuinely need TransactionTestCase semantics.

    Use this only when the test actually requires real DB commits (e.g. testing on_commit callbacks
    with a live server). For most tests, use @pytest.mark.django_db() instead.
    """
    return django_db_with_data()


def restore_serialized_database(connection: BaseDatabaseWrapper, serialized_contents: str) -> None:
    """Put a reused test database back to the state it was serialized in.

    A `django_db(transaction=True)` test flushes the database and lets `post_migrate` re-create
    the content type and permission rows, which come back with new primary keys. Replaying the
    snapshot on top of that would insert the old primary key for a natural key that already
    exists, so the database is emptied first. Emptying it and refilling it share a transaction:
    a restore that fails halfway would otherwise leave the reused database with nothing in it.
    """
    with transaction.atomic(using=connection.alias):
        call_command(
            "flush",
            verbosity=0,
            interactive=False,
            database=connection.alias,
            reset_sequences=False,
            inhibit_post_migrate=True,
        )
        connection.creation.deserialize_db_from_string(serialized_contents)
