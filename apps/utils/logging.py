"""Custom logging utilities for Open Chat Studio."""

import logging
from contextvars import ContextVar

_celery_task_id: ContextVar[str | None] = ContextVar("celery_task_id", default=None)
_celery_task_name: ContextVar[str | None] = ContextVar("celery_task_name", default=None)


class ContextVarFilter(logging.Filter):
    """Log filter that adds team and request_id from context vars."""

    def filter(self, record):
        from apps.audit.transaction import get_audit_transaction_id
        from apps.teams.utils import get_current_team

        team = get_current_team()
        record.team = team.slug if team else None
        if request_id := get_audit_transaction_id():
            record.request_id = request_id
        return True


class CeleryContextFilter(logging.Filter):
    """Log filter that adds Celery task context using contextvars for async safety."""

    @staticmethod
    def set_task_context(task_id: str, task_name: str):
        _celery_task_id.set(task_id)
        _celery_task_name.set(task_name)

    @staticmethod
    def clear_task_context():
        _celery_task_id.set(None)
        _celery_task_name.set(None)

    def filter(self, record):
        if task_id := _celery_task_id.get():
            record.task_id = task_id
        if task_name := _celery_task_name.get():
            record.task_name = task_name
        return True
