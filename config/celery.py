import os
import pathlib

from celery import Celery, signals
from celery.app import trace

from apps.utils.logging import CeleryContextFilter

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

#: Touched once the worker is consuming its queues and removed when it shuts down, so a container
#: orchestrator can gate a rolling deploy on readiness rather than on process start. Celery offers
#: no cheap readiness probe -- `celery inspect ping` costs a second full app import, which a worker
#: sized for its own workload cannot absorb -- so the worker reports readiness itself. Unset
#: outside ECS, where nothing reads it.
READY_FILE = os.environ.get("CELERY_READY_FILE")

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


@signals.worker_ready.connect
def on_worker_ready(**_):
    if READY_FILE:
        pathlib.Path(READY_FILE).touch()


@signals.worker_shutdown.connect
def on_worker_shutdown(**_):
    if READY_FILE:
        pathlib.Path(READY_FILE).unlink(missing_ok=True)
