"""Fixtures for the version endpoints (#4142)."""

from unittest.mock import patch

import pytest

from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def chatbot(team):
    """`ChatbotFactory` wires Start -> End, so the pipeline validates cleanly and can go live."""
    return ChatbotFactory.create(team=team, name="Support bot")


@pytest.fixture()
def client(chatbot):
    return ApiTestClient(chatbot.team.members.first(), chatbot.team)


@pytest.fixture()
def dispatch():
    """The Celery hand-off, stubbed: the suite has no broker, and what these endpoints are
    responsible for is what they asked the worker to do, not what the worker then did."""
    with patch("apps.experiments.tasks.async_create_experiment_version.apply_async") as apply_async:
        yield apply_async


@pytest.fixture()
def llm_provider(team):
    """A provider and a model of the same type, which is what pairs them for an LLM node."""
    provider = LlmProviderFactory.create(team=team, type="openai")
    model = LlmProviderModelFactory.create(team=team, type="openai", name="gpt-4o")
    return provider, model


def versions_url(chatbot) -> str:
    return f"/api/v2/chatbots/{chatbot.public_id}/versions/"


def nodes_url(chatbot) -> str:
    return f"/api/v2/chatbots/{chatbot.public_id}/pipeline/nodes/"


def strand_the_end_node(chatbot) -> None:
    """Drop the one edge, leaving End unreachable -- the commonest way a half-built graph is invalid.

    Written straight into ``Pipeline.data`` because that is where the edges live (ADR-0049), and
    because a test about publishing should not fail when the unwire endpoint changes.
    """
    pipeline = chatbot.pipeline
    pipeline.data["edges"] = []
    pipeline.save(update_fields=["data"])


def status_url(chatbot) -> str:
    return f"/api/v2/chatbots/{chatbot.public_id}/versions/status/"


def version_url(chatbot, version_number: int) -> str:
    return f"/api/v2/chatbots/{chatbot.public_id}/versions/{version_number}/"
