"""Tests for the DATABASES config builder.

Guards against the regression in which ``CONN_HEALTH_CHECKS`` and
``DISABLE_SERVER_SIDE_CURSORS`` were only set on the discrete-variable branch, leaving them
unset — and ``DJANGO_DISABLE_SERVER_SIDE_CURSORS`` a silent no-op — wherever ``DATABASE_URL``
is used, which is every deployed environment (see config/db.py).
"""

import environ
import pytest

from config.db import get_database_config

DATABASE_URL = "postgres://user:pw@dbhost:5432/ocs"


def _env(**environ_vars: str) -> environ.Env:
    """An Env reading only ``environ_vars``, so tests don't see the developer's own environment.

    ``ENVIRON`` is a class attribute pointing at ``os.environ``; shadowing it per instance keeps
    each case isolated without mutating the real process environment.
    """
    env = environ.Env()
    env.ENVIRON = environ_vars  # ty: ignore[invalid-assignment]
    return env


@pytest.fixture(params=["database_url", "discrete_vars"])
def env(request) -> environ.Env:
    """Both supported ways of pointing at a database, so every assertion covers both."""
    if request.param == "database_url":
        return _env(DATABASE_URL=DATABASE_URL)
    return _env(DJANGO_DATABASE_HOST="dbhost", DJANGO_DATABASE_NAME="ocs")


def test_connection_is_configured(env):
    config = get_database_config(env, debug=False)["default"]
    assert "postgresql" in config["ENGINE"]
    assert config["NAME"] == "ocs"
    assert config["HOST"] == "dbhost"


def test_django_level_keys_are_applied(env):
    config = get_database_config(env, debug=False)["default"]
    assert config["CONN_HEALTH_CHECKS"] is True
    assert config["DISABLE_SERVER_SIDE_CURSORS"] is False


def test_server_side_cursors_can_be_disabled(env):
    """The env var has to reach Django regardless of how the connection was configured."""
    env.ENVIRON["DJANGO_DISABLE_SERVER_SIDE_CURSORS"] = "True"

    config = get_database_config(env, debug=False)["default"]

    assert config["DISABLE_SERVER_SIDE_CURSORS"] is True


def test_pool_is_enabled_by_default(env):
    config = get_database_config(env, debug=False)["default"]

    assert config["OPTIONS"]["pool"] == {"min_size": 2, "max_size": 35, "timeout": 10}
    # Persistent connections are the pool's job; leaving CONN_MAX_AGE set would double up on it.
    assert "CONN_MAX_AGE" not in config


def test_conn_max_age_replaces_the_pool_when_it_is_disabled(env):
    env.ENVIRON["DJANGO_DATABASE_USE_POOL"] = "False"
    env.ENVIRON["DJANGO_DATABASE_CONN_MAX_AGE"] = "600"

    config = get_database_config(env, debug=False)["default"]

    assert config["CONN_MAX_AGE"] == 600
    assert "pool" not in config["OPTIONS"]


@pytest.mark.parametrize(
    ("debug", "expected"),
    [
        pytest.param(False, "require", id="deployed-requires-tls"),
        pytest.param(True, "prefer", id="local-dev-may-skip-tls"),
    ],
)
def test_sslmode_follows_debug(env, debug, expected):
    assert get_database_config(env, debug=debug)["default"]["OPTIONS"]["sslmode"] == expected


def test_sslmode_can_be_overridden(env):
    env.ENVIRON["DJANGO_DATABASE_SSLMODE"] = "disable"

    assert get_database_config(env, debug=False)["default"]["OPTIONS"]["sslmode"] == "disable"
