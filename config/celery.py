import os

from celery import Celery, signals
from celery.app import trace

# Don't use connection pooling in Celery
os.environ["DJANGO_DATABASE_USE_POOL"] = "false"
os.environ["DJANGO_DATABASE_CONN_MAX_AGE"] = "0"

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("open_chat_studio")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()

app.conf.result_expires = 86400  # expire results in redis in 1 day

trace.LOG_SUCCESS = """\
Task %(name)s[%(id)s] succeeded in %(runtime)ss\
"""

# Set this to 1 since many of our tasks are long-running
worker_prefetch_multiplier = 1
worker_max_tasks_per_child = 100  # Restart worker periodically
task_acks_late = True


# Fix for SSL connection errors with gevent + psycopg3
# psycopg3 connections with SSL are not greenlet-safe when shared across greenlets.
# Django doesn't close connections after Celery tasks (no request_finished signal),
# so connections persist and get shared, causing "SSL error: decryption failed or bad record mac"
# Solution: Force connection cleanup before/after each task
@signals.task_prerun.connect
def close_db_connections_before_task(**kwargs):
    """Close database connections before task execution to ensure each greenlet gets a fresh connection"""
    from django.db import close_old_connections

    close_old_connections()


@signals.task_postrun.connect
def close_db_connections_after_task(**kwargs):
    """Close database connections after task execution to prevent connection sharing across greenlets"""
    from django.db import close_old_connections

    close_old_connections()


@signals.worker_process_init.connect
def setup_worker_process(**kwargs):
    """Initialize worker process with clean database connections"""
    from django.db import close_old_connections

    close_old_connections()
