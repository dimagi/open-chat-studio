import pytest

from apps.teams.backends import create_default_groups
from apps.teams.utils import set_current_team, unset_current_team
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamFactory


@pytest.fixture()
def team_with_roles(db):
    """`create_default_groups()` is explicit so the DB-backed groups match backends.py even
    though pytest runs with --reuse-db."""
    create_default_groups()
    team = TeamFactory.create()
    token = set_current_team(team)
    try:
        yield team
    finally:
        unset_current_team(token)


@pytest.fixture()
def llm(team):
    """A provider and a model of the same type, which is what pairs them for an LLM node."""
    provider = LlmProviderFactory.create(team=team, type="openai")
    model = LlmProviderModelFactory.create(team=team, type="openai", name="gpt-4o")
    return provider, model
