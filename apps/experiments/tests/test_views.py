import pytest
from django.urls import reverse

from apps.experiments.models import Experiment
from apps.experiments.views.experiment import _source_material_is_missing
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
    "create_source_material,promp_str",
    [
        (True, "You're an assistant"),
        (True, "Answer questions from this source: {source_material}"),
        (False, "You're an assistant"),
    ],
)
def test_experiment_does_not_require_source_material(db, create_source_material, promp_str):
    """Tests the `_source_material_is_missing` method"""
    material = None
    if create_source_material:
        material = experiment_factory.SourceMaterialFactory()
    experiment = experiment_factory.ExperimentFactory(chatbot_prompt__prompt=promp_str, source_material=material)
    assert _source_material_is_missing(experiment) is False


@pytest.mark.parametrize(
    "source_material,promp_str",
    [
        (None, "Answer questions from this source: {source_material}"),
    ],
)
def test_source_material_is_missing(db, source_material, promp_str):
    experiment = experiment_factory.ExperimentFactory(chatbot_prompt__prompt=promp_str, source_material=source_material)
    assert _source_material_is_missing(experiment) is True
