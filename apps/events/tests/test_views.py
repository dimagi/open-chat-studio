import pytest
from django.urls import reverse


@pytest.fixture()
def authed_client(team_with_users, client):
    user = team_with_users.members.first()
    client.force_login(user)
    return client


@pytest.mark.parametrize(
    "action_type",
    ["log", "send_message_to_bot", "end_conversation", "schedule_trigger", "pipeline_start"],
)
@pytest.mark.django_db()
def test_action_params_form_view_renders_each_action_type(action_type, experiment, authed_client):
    url = reverse(
        "chatbots:events:action_params_form",
        args=[experiment.team.slug, experiment.id],
    )
    response = authed_client.get(url, {"action_type": action_type})
    assert response.status_code == 200


@pytest.mark.django_db()
def test_action_params_form_view_400_for_invalid_action_type(experiment, authed_client):
    url = reverse(
        "chatbots:events:action_params_form",
        args=[experiment.team.slug, experiment.id],
    )
    response = authed_client.get(url, {"action_type": "bogus"})
    assert response.status_code == 400
