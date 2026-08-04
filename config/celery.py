import os

from celery import Celery, signals
from celery.app import trace

from apps.utils.logging import CeleryContextFilter

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
trace.LOG_SUCCESS = "Task %(name)s[%(id)s] succeeded in %(runtime)ss"  # ty: ignore[invalid-assignment]

app.conf.update(
    result_expires=86400,  # expire results in redis in 1 day
    worker_hijack_root_logger=False,
    worker_log_format="%(message)s",
    worker_task_log_format="%(message)s",
)


@signals.task_prerun.connect
def on_task_prerun(sender, task_id, task, args, kwargs, **_):
    CeleryContextFilter.set_task_context(task_id, task.name)


@signals.task_postrun.connect
def on_task_postrun(sender, **_):
    from apps.teams.utils import unset_current_team  # noqa: PLC0415 - apps aren't fully loaded when celery loads

    CeleryContextFilter.clear_task_context()
    unset_current_team()
