def django_db_with_data():
    """Shortcut decorator to mark a test function as requiring the database with data from migrations.

    This is needed because of other tests that flush the database after each test
    e.g. apps.api.tests.test_openai_api.test_chat_completion

    See also `apps.conftest._django_db_restore_serialized`.
    """
    import pytest

    def _inner(func):
        return pytest.mark.django_db(
            serialized_rollback=True,  # load the serialize DB state
            transaction=True,  # required for serialized_rollback to work
        )(func)

    return _inner


def django_db_transactional():
    """Shortcut decorator to mark a test function as a transactional test.

    This is just an alias for django_db_with_data() but kept separate for clarity.

    An alternative would be to use the pytest.mark.django_db(transaction=True) decorator
    (without `serialized_rollback=True`), but we rely on
    the serialized_rollback=True to load the serialized DB state which includes the content types and permissions.
    """
    return django_db_with_data()
