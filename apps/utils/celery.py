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
