"""
Taskiq configuration for Django integration.

This module sets up the taskiq broker and handles Django database connection management.

taskiq worker config.tkq:broker --tasks-pattern **/tasks_async.py
"""

import os
from pathlib import Path

import django
import environ
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
env.read_env(os.path.join(BASE_DIR, ".env"))

# Initialize Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django.setup()

from django.db import connections  # noqa E402

broker = ListQueueBroker(
    url=env("REDIS_URL"),
).with_result_backend(
    RedisAsyncResultBackend(
        redis_url=env("REDIS_URL"),
    )
)

# Critical: Database connection management for Django + Taskiq
# Django connections are thread-local and don't work well with async workers
# We need to properly close connections before and after task execution


@broker.on_event("worker_startup")
async def worker_startup(state):
    """
    Called when a worker starts up.
    Ensures Django is properly initialized.
    """
    print("Worker starting up...")
    connections.close_all()
    print("Worker startup complete")


@broker.on_event("worker_shutdown")
async def worker_shutdown(state):
    """
    Called when a worker shuts down.
    Ensures all database connections are closed.
    """
    print("Worker shutting down...")
    connections.close_all()
    print("Worker shutdown complete")


@broker.task_preprocessor
async def close_old_connections(context):
    """
    Close database connections before each task execution.
    This prevents stale connections and thread-safety issues.
    """
    connections.close_all()


@broker.task_postprocessor
async def close_connections_after_task(context):
    """
    Close database connections after each task execution.
    This is critical for proper connection management in async environments.
    """
    connections.close_all()
