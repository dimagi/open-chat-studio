from unittest import mock

import pytest

from apps.service_providers.llm_service.default_models import MODELS_BY_KEY, Model
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


def test_chat_pickers_leave_out_a_decision_model_of_a_chat_provider(providers):
    team, openai, _jev = providers
    LlmProviderModelFactory.create(team=team, type="openai", name="gpt-decide")

    decision_model = Model("gpt-decide", 1000, chat=False, decisions=True)
    with mock.patch.dict(MODELS_BY_KEY, {("openai", "gpt-decide"): decision_model}):
        offered = [model["text"] for model in get_llm_provider_choices(team)[openai.id]["models"]]
        grouped = get_models_by_team_grouped_by_provider(team)["OpenAI"]

    assert f"OpenAI: gpt-x (custom for {team.name})" in offered
    assert not [text for text in offered if "gpt-decide" in text]
    assert "gpt-x" in grouped
    assert "gpt-decide" not in grouped


def test_chat_pickers_leave_out_googles_decision_model(db):
    team = TeamFactory.create()
    google = LlmProviderFactory.create(team=team, type="google")
    LlmProviderModelFactory.create(team=team, type="google", name="gemini-3.8-flash")
    LlmProviderModelFactory.create(team=team, type="google", name="gemini-decision-1")

    offered = [model["text"] for model in get_llm_provider_choices(team)[google.id]["models"]]
    grouped = get_models_by_team_grouped_by_provider(team)["Google Gemini"]

    assert [text for text in offered if "gemini-3.8-flash" in text]
    assert not [text for text in offered if "gemini-decision-1" in text]
    assert "gemini-3.8-flash" in grouped
    assert "gemini-decision-1" not in grouped
    assert get_first_llm_provider_by_team(team.id) == google
