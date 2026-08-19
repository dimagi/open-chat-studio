"""Database configuration.

Kept out of ``config.settings`` so that both ways of configuring the connection — a single
``DATABASE_URL`` or the discrete ``DJANGO_DATABASE_*`` variables — can be exercised by tests.

The two must agree on the Django-level keys. ``env.db()`` only ever emits
engine/name/user/password/host/port, so any other key has to be applied to whichever config
the branch produced: a key set inside the ``else`` branch alone is dead in every environment
that uses ``DATABASE_URL``, which is all of them except a bare local checkout.
"""

import environ


def get_database_config(env: environ.Env, *, debug: bool) -> dict:
    """Build the ``DATABASES`` setting for the ``default`` connection."""
    if "DATABASE_URL" in env:
        config = env.db()
    else:
        config = {
            "ENGINE": "django.db.backends.postgresql_psycopg2",
            "NAME": env("DJANGO_DATABASE_NAME", default="open_chat_studio"),
            "USER": env("DJANGO_DATABASE_USER", default="postgres"),
            "PASSWORD": env("DJANGO_DATABASE_PASSWORD", default="***"),
            "HOST": env("DJANGO_DATABASE_HOST", default="localhost"),
            "PORT": env("DJANGO_DATABASE_PORT", default="5432"),
        }

    config["CONN_HEALTH_CHECKS"] = True
    # Server-side cursors (Django's implementation of `QuerySet.iterator()` on Postgres) are
    # declared `WITH HOLD` outside an atomic block, which makes RDS Proxy pin the session to a
    # backend connection for the life of the client connection. Django reads this key from the
    # top level of the connection's settings dict, not from OPTIONS, so it cannot be smuggled
    # in through the DATABASE_URL query string.
    config["DISABLE_SERVER_SIDE_CURSORS"] = env.bool("DJANGO_DISABLE_SERVER_SIDE_CURSORS", default=False)

    options: dict = config.setdefault("OPTIONS", {})  # ty: ignore[invalid-assignment]
    if env.bool("DJANGO_DATABASE_USE_POOL", True):
        config.pop("CONN_MAX_AGE", None)
        # See https://www.psycopg.org/psycopg3/docs/api/pool.html#psycopg_pool.ConnectionPool
        options["pool"] = {
            "min_size": env.int("DJANGO_DATABASE_POOL_MIN_SIZE", default=2),
            "max_size": env.int("DJANGO_DATABASE_POOL_MAX_SIZE", default=35),
            "timeout": env.int("DJANGO_DATABASE_POOL_TIMEOUT", default=10),
        }
    else:
        config["CONN_MAX_AGE"] = env.int("DJANGO_DATABASE_CONN_MAX_AGE", 0)

    # RDS Proxy requires TLS. psycopg3 defaults to sslmode=prefer which falls back to
    # non-SSL on handshake failure, which the proxy rejects. sslmode=require forces SSL
    # without the non-SSL fallback. Override with DJANGO_DATABASE_SSLMODE if needed
    # (e.g. set to "prefer" for local dev without TLS).
    options["sslmode"] = env("DJANGO_DATABASE_SSLMODE", default="prefer" if debug else "require")

    return {"default": config}
