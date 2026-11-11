import pytest

from apps.service_providers.utils import get_first_llm_provider_by_team
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_get_first_llm_provider_by_team_skips_a_router_only_provider():
    """The first provider seeds a new chatbot's LLM node, which cannot use Jev."""
    team = TeamFactory.create()
    LlmProviderFactory.create(team=team, type="jev")
    openai = LlmProviderFactory.create(team=team, type="openai")

    assert get_first_llm_provider_by_team(team.id) == openai
