import pytest


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
