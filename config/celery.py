import os

import structlog
from celery import Celery, signals
from celery.app import trace
from django import db

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

# don't log task result
trace.LOG_SUCCESS = """\
Task %(name)s[%(id)s] succeeded in %(runtime)ss\
"""

worker_max_tasks_per_child = 100  # Restart worker periodically
task_acks_late = True

app.conf.update(
    result_expires=86400,  # expire results in redis in 1 day
    worker_hijack_root_logger=False,
    worker_log_format="%(message)s",
    worker_task_log_format="%(message)s",
)


@signals.task_prerun.connect
def on_task_prerun(sender, task_id, task, args, kwargs, **_):
    structlog.contextvars.bind_contextvars(task_id=task_id, task_name=task.name)


def close_db_connection(sender, **kwargs):
    if getattr(sender.request, "is_eager", False):
        return

    # Copied from https://github.com/celery/celery/blob/main/celery/fixups/django.py
    # Can be removed when upgrading Celery > 5.5.3
    # (once https://github.com/celery/celery/commit/da4a80dc449301fde4355153b47af8c42caed37c is released)
    for conn in db.connections.all(initialized_only=True):
        try:
            conn.close()
        except db.InterfaceError:
            pass
        except db.DatabaseError as exc:
            str_exc = str(exc)
            if "closed" not in str_exc and "not connected" not in str_exc:
                raise


signals.task_prerun.connect(close_db_connection)
signals.task_postrun.connect(close_db_connection)
