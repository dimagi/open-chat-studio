import dataclasses
import datetime
import typing

import celery
from celery.app import app_or_default
from django.conf import settings
from health_check.base import HealthCheck
from health_check.exceptions import ServiceUnavailable
from redis.asyncio import Redis as RedisClient

from apps.utils.celery import Queues


@dataclasses.dataclass
class CeleryQueueCheck(HealthCheck):
    """Verify at least one Celery worker is actively consuming a specific queue."""

    CORRECT_PING_RESPONSE: typing.ClassVar[dict[str, str]] = {"ok": "pong"}

    label: str
    queue: str
    app: celery.Celery = dataclasses.field(default_factory=app_or_default, repr=False)
    timeout: datetime.timedelta = dataclasses.field(default=datetime.timedelta(seconds=1), repr=False)

    def run(self):
        timeout = self.timeout.total_seconds()

        try:
            active_queues = self.app.control.inspect(timeout=timeout).active_queues() or {}
        except OSError as e:
            raise ServiceUnavailable("IOError") from e

        workers_on_queue = [
            worker for worker, queues in active_queues.items() if any(queue["name"] == self.queue for queue in queues)
        ]
        if not workers_on_queue:
            raise ServiceUnavailable(f"No worker for Celery queue {self.label!r} ({self.queue})")

        try:
            ping_result = self.app.control.ping(destination=workers_on_queue, timeout=timeout) or []
        except OSError as e:
            raise ServiceUnavailable("IOError") from e

        if not any(response == self.CORRECT_PING_RESPONSE for reply in ping_result for response in reply.values()):
            raise ServiceUnavailable(f"No worker for Celery queue {self.label!r} ({self.queue})")


def _general_checks():
    return [
        "health_check.checks.Database",
        "health_check.checks.Cache",
        ("health_check.contrib.redis.Redis", {"client_factory": lambda: RedisClient.from_url(settings.REDIS_URL)}),
    ]


def _queue_check(queue: Queues):
    return (CeleryQueueCheck, {"label": queue.name.lower(), "queue": queue.value})


def _check_subsets():
    return {
        "general": _general_checks(),
        "celery": [_queue_check(queue) for queue in Queues],
        **{f"queue-{queue.name.lower()}": [_queue_check(queue)] for queue in Queues},
    }


CHECK_SUBSETS = _check_subsets()
