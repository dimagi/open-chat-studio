from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.service_provider_factories import ModerationProviderFactory
from apps.utils.tests.moderation_helpers import select_moderation_provider


@pytest.fixture()
def moderated_session():
    """A session whose team has moderation turned on with a provider."""
    session = ExperimentSessionFactory.create()
    team = session.experiment.team
    select_moderation_provider(team, ModerationProviderFactory.create(team=team))
    return session


@pytest.fixture()
def moderation_task():
    """Patch the moderation task. Starting it returns the id "task-1"; set `get.return_value` (or
    `get.side_effect`) to what waiting for its result returns (or raises)."""
    with (
        patch("apps.moderation.services.moderate_message") as task,
        patch("apps.channels.stages.terminal.AsyncResult") as async_result,
    ):
        task.delay.return_value.id = "task-1"
        yield SimpleNamespace(task=task, async_result=async_result, get=async_result.return_value.get)
