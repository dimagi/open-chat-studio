from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture()
def moderation_task():
    """Patch the moderation task. Starting it returns the id "task-1"; set `get.return_value` (or
    `get.side_effect`) to what waiting for its result returns (or raises)."""
    with (
        patch("apps.moderation.services.moderate_message") as task,
        patch("apps.moderation.services.AsyncResult") as async_result,
    ):
        task.delay.return_value.id = "task-1"
        yield SimpleNamespace(task=task, async_result=async_result, get=async_result.return_value.get)
