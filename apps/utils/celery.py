class TaskbadgerTaskWrapper:
    def __init__(self, celery_task):
        self.celery_task = celery_task
        self.task = celery_task.taskbadger_task

    def set_total(self, count: int):
        if self.task:
            self.task.set_value(count)

    def set_progress(self, progress: int, total: int = None):
        if self.task:
            kwargs = {"value": progress}
            if total:
                kwargs["value_max"] = total
            self.task.update(**kwargs)

    def increment_total(self, count: int = 1):
        if self.task:
            self.task.set_value_max(self.task.value_max + count)
