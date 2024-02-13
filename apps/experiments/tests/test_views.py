from contextlib import nullcontext as does_not_raise
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from waffle.testutils import override_flag

from apps.experiments.models import Experiment
from apps.experiments.views.experiment import ExperimentForm, _validate_prompt_variables
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ConsentFormFactory, SourceMaterialFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory


def test_create_experiment_success(db, client, team_with_users):
    user = team_with_users.members.first()
    source_material = SourceMaterialFactory(team=team_with_users)
    consent_form = ConsentFormFactory(team=team_with_users)
    LlmProviderFactory(team=team_with_users)
    client.force_login(user)

    post_data = {
        "name": "some name",
        "description": "Some description",
        "prompt_text": "You are a helpful assistant",
        "source_material": source_material.id if source_material else "",
        "consent_form": consent_form.id,
        "temperature": 0.7,
        "llm_provider": LlmProviderFactory(team=team_with_users).id,
        "llm": "gpt-3.5",
        "max_token_limit": 100,
    }

    response = client.post(reverse("experiments:new", args=[team_with_users.slug]), data=post_data)
    assert response.status_code == 302
    experiment = Experiment.objects.filter(owner=user).first()
    assert experiment is not None


@override_flag("assistants", active=True)
@pytest.mark.parametrize(
    ("with_assistant", "with_prompt", "with_llm_provider", "with_llm_model", "errors"),
    [
        (True, False, False, False, {}),
        (False, True, True, True, {}),
        (False, False, True, True, {"prompt_text"}),
        (False, True, False, True, {"llm_provider"}),
        (False, True, True, False, {"llm"}),
    ],
)
def test_experiment_form_with_assistants(
    with_assistant, with_prompt, with_llm_provider, with_llm_model, errors, db, team_with_users
):
    assistant = OpenAiAssistantFactory(team=team_with_users)
    request = mock.Mock()
    request.team = team_with_users
    llm_provider = LlmProviderFactory(team=team_with_users)
    form = ExperimentForm(
        request,
        data={
            "name": "some name",
            "assistant": assistant.id if with_assistant else None,
            "prompt_text": "text" if with_prompt else None,
            "llm_provider": llm_provider.id if with_llm_provider else None,
            "llm": "gpt4" if with_llm_model else None,
            "temperature": 0.7,
            "max_token_limit": 10,
            "consent_form": ConsentFormFactory(team=team_with_users).id,
        },
    )
    assert form.is_valid() == bool(not errors), form.errors
    for error in errors:
        assert error in form.errors


@pytest.mark.parametrize(
    ("source_material", "prompt_str", "expectation"),
    [
        (None, "You're an assistant", does_not_raise()),
        ("something", "You're an assistant", does_not_raise()),
        ("something", "Answer questions from this source: {source_material}", does_not_raise()),
        (None, "Answer questions from this source: {source_material}", pytest.raises(ValidationError)),
        (None, "Answer questions from this source: {bob}", pytest.raises(ValidationError)),
        ("something", "Answer questions from this source: {bob}", pytest.raises(ValidationError)),
    ],
)
def test_prompt_variable_validation(source_material, prompt_str, expectation):
    with expectation:
        _validate_prompt_variables(
            {
                "source_material": source_material,
                "prompt_text": prompt_str,
            }
        )
