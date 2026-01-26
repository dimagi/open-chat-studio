"""Custom logging utilities for Open Chat Studio."""

import logging


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
    """Log filter that adds Celery task context."""

    _task_id = None
    _task_name = None

    @classmethod
    def set_task_context(cls, task_id: str, task_name: str):
        cls._task_id = task_id
        cls._task_name = task_name

    @classmethod
    def clear_task_context(cls):
        cls._task_id = None
        cls._task_name = None

    def filter(self, record):
        if self._task_id:
            record.task_id = self._task_id
        if self._task_name:
            record.task_name = self._task_name
        return True
