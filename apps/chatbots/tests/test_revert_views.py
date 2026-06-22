from unittest.mock import Mock, patch

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.pipelines.tests.utils import create_pipeline_model, end_node, llm_response_with_prompt_node, start_node
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


def _changed_field_names(version_details):
    """Recursively collect the names of all fields marked changed in a compared VersionDetails."""
    names = []

    def walk(details):
        for field in details.fields:
            if field.changed:
                names.append(field.name)
            if field.raw_value_version:
                walk(field.raw_value_version)
            for result in field.queryset_results or []:
                if result.raw_value_version:
                    walk(result.raw_value_version)

    walk(version_details)
    return names


@pytest.mark.django_db()
def test_revert_chatbot_version_view(client, team_with_users):
    team = team_with_users
    user = team.members.first()
    user.user_permissions.add(Permission.objects.get(codename="change_experiment"))
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team, name="Original", owner=user)
    version = experiment.create_new_version(make_default=True)
    experiment.name = "Modified"
    experiment.save()

    url = reverse("chatbots:revert-version", args=[team.slug, experiment.id, version.version_number])
    response = client.post(url)

    assert response.status_code == 302
    assert response.url.endswith("#versions")
    experiment.refresh_from_db()
    assert experiment.name == "Original"


@pytest.mark.django_db()
def test_revert_confirm_shows_diff_and_overwrite_warning(client, team_with_users):
    team = team_with_users
    user = team.members.first()
    user.user_permissions.add(Permission.objects.get(codename="change_experiment"))
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team, name="Original", owner=user)
    version = experiment.create_new_version(make_default=True)
    experiment.name = "Modified"
    experiment.save()

    url = reverse("chatbots:revert-version-confirm", args=[team.slug, experiment.id, version.version_number])
    response = client.get(url)

    assert response.status_code == 200
    assert response.context["version_details"].fields_changed is True
    assert response.context["has_unreleased_changes"] is True
    content = response.content.decode()
    assert "unreleased changes" in content
    # The diff renders the current working value and the target version value.
    assert "Original" in content
    assert "Modified" in content


@pytest.mark.django_db()
def test_revert_confirm_no_changes_when_working_matches_version(client, team_with_users):
    team = team_with_users
    user = team.members.first()
    user.user_permissions.add(Permission.objects.get(codename="change_experiment"))
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team, name="Original", owner=user)
    version = experiment.create_new_version(make_default=True)

    url = reverse("chatbots:revert-version-confirm", args=[team.slug, experiment.id, version.version_number])
    response = client.get(url)

    assert response.status_code == 200
    assert response.context["version_details"].fields_changed is False
    assert response.context["has_unreleased_changes"] is False
    assert "will make no changes" in response.content.decode()


@pytest.mark.django_db()
@patch("apps.assistants.sync.push_assistant_to_openai", Mock())
def test_revert_confirm_diff_preserved_when_working_matches_latest(client, team_with_users):
    """The unreleased-changes check must not overwrite the working-vs-target diff.

    Regression: the check compares the working state against the *latest* version. If it shares a
    VersionDetails instance with the diff (working vs target), its in-place comparison re-marks
    pipeline node fields as unchanged (they match the latest version), hiding real differences
    against the target.
    """
    team = team_with_users
    user = team.members.first()
    user.user_permissions.add(Permission.objects.get(codename="change_experiment"))
    client.force_login(user)

    experiment = ExperimentFactory.create(team=team, name="Bot", owner=user)
    provider = LlmProviderFactory.create(team=team)
    provider_model = LlmProviderModelFactory.create(team=team)
    llm = llm_response_with_prompt_node(str(provider.id), str(provider_model.id), prompt="PROMPT A", name="llm")
    nodes = [start_node(), llm, end_node()]
    create_pipeline_model(nodes, pipeline=experiment.pipeline)
    experiment.pipeline.save(update_fields=["data"])

    v1 = experiment.create_new_version(make_default=True)

    # Change the prompt and publish it as the latest version, so the working state matches latest.
    llm["params"]["prompt"] = "PROMPT B"
    create_pipeline_model(nodes, pipeline=experiment.pipeline)
    experiment.pipeline.save(update_fields=["data"])
    experiment.create_new_version()

    url = reverse("chatbots:revert-version-confirm", args=[team.slug, experiment.id, v1.version_number])
    response = client.get(url)

    assert response.status_code == 200
    # Working matches the latest version, so there are no unreleased changes...
    assert response.context["has_unreleased_changes"] is False
    # ...but the prompt still differs from v1 and that diff must be shown.
    assert "prompt" in _changed_field_names(response.context["version_details"])
    content = response.content.decode()
    assert "PROMPT A" in content
    assert "PROMPT B" in content
