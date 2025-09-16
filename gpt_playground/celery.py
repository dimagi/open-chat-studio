import logging
import os

from celery import Celery
from celery.app import trace
from celery.signals import task_prerun
from django.db import connections

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gpt_playground.settings")

app = Celery("gpt_playground")

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

logger = logging.getLogger("ocs.debug")


@task_prerun.connect
def log_pool_stats(*args, **kwargs):
    for conn in connections.all():
        logger.info(conn.pool.get_stats())
