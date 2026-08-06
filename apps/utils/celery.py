from enum import StrEnum


class Queues(StrEnum):
    """The Celery queues tasks are routed to.

    Every task defined under ``apps/`` must declare one explicitly via ``@shared_task(queue=...)``;
    ``apps/utils/tests/test_celery_queues.py`` fails the build if one doesn't. The point of the
    split is that a backed-up evaluation run can never starve inbound chat messages of workers.

    A worker started without ``-Q`` consumes every queue in ``settings.CELERY_TASK_QUEUES``, so the
    single-worker setups (local dev, docker compose, Heroku, Kamal, self-hosters) keep working
    unchanged. Only production runs a dedicated worker per queue.
    """

    #: Latency-sensitive chat path: inbound message handlers, event triggers, outbound bot
    #: messages. Named "celery" because it is also ``task_default_queue`` — third-party and
    #: built-in tasks (e.g. ``celery.backend_cleanup``) fall through to it, and keeping the
    #: default name means nothing enqueued before this split was introduced was stranded.
    CHAT = "celery"

    #: Long-running or resource-intensive work: indexing, exports, imports, cleanup crons, and
    #: the evaluation control plane.
    BACKGROUND = "background"

    #: Evaluation fan-out only. Deliberately excludes the tasks that dispatch and drain it
    #: (``coordinate_evaluation_runs``, ``drive_evaluation_run``, ``finalize_evaluation_run``) so
    #: a saturated queue can never block its own drain.
    EVALUATIONS = "evaluations"


class TaskbadgerTaskWrapper:
    """Wrapper for Celery tasks to provide progress reporting via taskbadger.

    This class safely updates task progress without failing if taskbadger_task is None.
    """

    def __init__(self, celery_task):
        self.celery_task = celery_task
        self.task = celery_task.taskbadger_task

    def set_total(self, count: int):
        if self.task:
            self.task.safe_update(value=count)

    def set_progress(self, progress: int, total: int | None = None):
        if self.task:
            kwargs = {"value": progress}
            if total:
                kwargs["value_max"] = total
            self.task.safe_update(**kwargs)
