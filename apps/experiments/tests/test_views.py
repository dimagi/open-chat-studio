from contextlib import nullcontext as does_not_raise

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.experiments.models import Experiment
from apps.experiments.views.experiment import ExperimentForm, _validate_prompt_variables
from apps.utils.factories import experiment as experiment_factory


def test_create_experiment_success(db, client, team_with_users):
    user = team_with_users.members.first()
    source_material = experiment_factory.SourceMaterialFactory(team=team_with_users)
    consent_form = experiment_factory.ConsentFormFactory(team=team_with_users)
    client.force_login(user)

    post_data = {
        "name": "some name",
        "description": "Some description",
        "prompt_text": "You are a helpful assistant",
        "source_material": source_material.id if source_material else "",
        "consent_form": consent_form.id,
        "temperature": 0.7,
        "llm": "gpt-3.5",
        "max_token_limit": 100,
    }

    response = client.post(reverse("experiments:new", args=[team_with_users.slug]), data=post_data)
    assert response.status_code == 302
    experiment = Experiment.objects.filter(owner=user).first()
    assert experiment is not None


@pytest.mark.parametrize(
    "source_material,prompt_str,expectation",
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
