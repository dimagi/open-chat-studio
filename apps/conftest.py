import os

import pytest
from django.db import connections

from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def experiment(team_with_users, db):
    return ExperimentFactory(team=team_with_users)


@pytest.fixture(autouse=True, scope="session")
def _django_db_restore_serialized(request: pytest.FixtureRequest, django_db_keepdb, django_db_blocker) -> None:
    """Restore database data at the end of the session. This is needed because we use transaction test cases
    in certain places which flush the DB. Individual tests that require the default DB data should
    use `apps.utils.pytest.django_db_with_data`.

    This fixture ensures that the data is preserved between test runs when `reuse-db` (`keepdb`) is being
    used.
    """
    yield

    if django_db_keepdb:
        with django_db_blocker.unblock():
            for connection in connections.all(initialized_only=True):
                if hasattr(connection, "_test_serialized_contents"):
                    connection.creation.deserialize_db_from_string(connection._test_serialized_contents)


@pytest.fixture(autouse=True, scope="session")
def _set_env():
    os.environ["UNIT_TESTING"] = "True"
