"""Translation and the prompt builder generate text, so they never offer a provider that only classifies."""

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.chat.models import ChatMessageType
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory, ParticipantFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.tests.session_helpers import session_url


@pytest.fixture()
def providers(experiment):
    team = experiment.team
    openai = LlmProviderFactory.create(team=team, type="openai")
    LlmProviderModelFactory.create(team=team, type="openai", name="gpt-x")
    jev = LlmProviderFactory.create(team=team, type="jev")
    jev_model = LlmProviderModelFactory.create(team=team, type="jev", name="jev-latest")
    return openai, jev, jev_model


@pytest.fixture()
def session(experiment):
    session = ExperimentSessionFactory.create(
        experiment=experiment, participant=ParticipantFactory.create(team=experiment.team, user=experiment.owner)
    )
    ChatMessageFactory.create(chat=session.chat, message_type=ChatMessageType.AI, content="Hello")
    return session


@pytest.mark.django_db()
def test_the_translation_form_leaves_out_jev(client, experiment, session, providers):
    openai, jev, _jev_model = providers
    client.force_login(experiment.owner)

    response = client.get(
        session_url(name="experiments:experiment_session_messages_view", experiment=experiment, session=session)
    )

    offered = {value for value, _label in response.context["translate_form_all"].fields["llm_provider"].choices}
    assert openai.id in offered
    assert jev.id not in offered


@pytest.mark.django_db()
def test_translating_with_jev_is_refused(client, experiment, session, providers):
    _openai, jev, jev_model = providers
    client.force_login(experiment.owner)

    response = client.post(
        session_url(name="experiments:translate_messages", experiment=experiment, session=session),
        {"llm_provider": jev.id, "llm_provider_model": jev_model.id, "language": "spa"},
    )

    assert [str(m) for m in get_messages(response.wsgi_request)] == ["Selected provider or model not found."]


@pytest.mark.django_db()
def test_the_prompt_builder_leaves_out_jev(client, team_with_users, providers):
    openai, _jev, _jev_model = providers
    client.force_login(team_with_users.members.first())

    response = client.get(reverse("experiments:experiments_prompt_builder", args=[team_with_users.slug]))

    assert response.context["llm_providers"] == [openai]
    assert set(response.context["llm_options"]) == {openai.id}
