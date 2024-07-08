def django_db_with_data(available_apps=("apps.experiments",)):
    """Shortcut decorator to mark a test function as requiring the database with data from migrations.

    This is needed because of other tests that flush the database after each test
    e.g. apps.api.tests.test_openai_api.test_chat_completion

    Args:
        available_apps (tuple, optional):
            The apps that are necessary for the test. Defaults to ("apps.experiments",).
            It is unclear which apps are necessary but these seem to work.
    """
    import pytest

    def _inner(func):
        return pytest.mark.django_db(
            serialized_rollback=True,  # load the serialize DB state
            transaction=True,  # required for serialized_rollback to work
            available_apps=available_apps,  # required to prevent teardown from failing
        )(func)

    return _inner
