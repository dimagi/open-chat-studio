import pytest

from apps.service_providers.utils import (
    get_dropdown_llm_model_choices,
    get_first_llm_provider_by_team,
    get_llm_provider_by_team,
    get_llm_provider_choices,
    get_models_by_team_grouped_by_provider,
)
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamFactory


@pytest.fixture()
def providers(db):
    """A team with a text-generating provider and a Jev provider, each with a model of its own type."""
    team = TeamFactory.create()
    jev = LlmProviderFactory.create(team=team, type="jev")
    LlmProviderModelFactory.create(team=team, type="jev", name="jev-latest")
    openai = LlmProviderFactory.create(team=team, type="openai")
    LlmProviderModelFactory.create(team=team, type="openai", name="gpt-x")
    return team, openai, jev


@pytest.mark.parametrize(
    "offered_provider_ids",
    [
        pytest.param(lambda team: [p.id for p in get_llm_provider_by_team(team)], id="translation-providers"),
        pytest.param(
            lambda team: [int(value.split(":")[0]) for value, _label in get_dropdown_llm_model_choices(team)],
            id="analysis-model-choices",
        ),
        pytest.param(lambda team: list(get_llm_provider_choices(team)), id="prompt-builder-options"),
        pytest.param(lambda team: [get_first_llm_provider_by_team(team.id).id], id="first-provider"),
    ],
)
def test_jev_is_not_offered_for_text_generation(providers, offered_provider_ids):
    team, openai, jev = providers

    offered = offered_provider_ids(team)

    assert openai.id in offered
    assert jev.id not in offered


def test_translation_models_leave_out_jev(providers):
    team, _openai, _jev = providers

    assert set(get_models_by_team_grouped_by_provider(team)) == {"OpenAI"}
