"""
Taskiq configuration for Django integration.

This module sets up the taskiq broker and handles Django database connection management.

taskiq worker config.tkq:broker --tasks-pattern '**/tasks_async.py' --fs-discover
"""

import os
from pathlib import Path
from typing import Any

import django
import environ
from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
env.read_env(os.path.join(BASE_DIR, ".env"))

# Initialize Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django.setup()

from django.db import connections  # noqa E402


class EnvContextManager:
    def __init__(self, **kwargs):
        self.env_vars = kwargs

    def __enter__(self):
        self.old_values = {key: os.environ.get(key) for key in self.env_vars if os.environ.get(key)}
        os.environ.update(self.env_vars)

    def __exit__(self, exc_type, exc_value, exc_traceback):
        os.environ.update(self.old_values)


class DjangoMiddleware(TaskiqMiddleware):
    def startup(self) -> None:
        self._close_connections()

    def shutdown(self) -> None:
        self._close_connections()

    def pre_execute(self, message: "TaskiqMessage") -> TaskiqMessage:
        self._close_connections()
        return message

    def post_save(self, message: "TaskiqMessage", result: "TaskiqResult[Any]") -> None:
        self._close_connections()

    def _close_connections(self):
        with EnvContextManager(DJANGO_ALLOW_ASYNC_UNSAFE="1"):
            connections.close_all()


broker = (
    ListQueueBroker(
        url=env("REDIS_URL"),
    )
    .with_result_backend(
        RedisAsyncResultBackend(
            redis_url=env("REDIS_URL"),
        )
    )
    .with_middlewares(DjangoMiddleware())
)
