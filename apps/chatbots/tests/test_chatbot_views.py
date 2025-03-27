import pytest
from django.template.response import TemplateResponse
from django.urls import reverse

from apps.experiments.models import Experiment
from apps.generics.views import generic_home
from apps.pipelines.models import Pipeline


@pytest.mark.django_db()
def test_generic_home():
    team_slug = "test-team"
    title = "Chatbots"
    table_url_name = "chatbots:table"
    new_url = "chatbots:new"

    response = generic_home(None, team_slug, title, table_url_name, new_url)

    # Check response type
    assert isinstance(response, TemplateResponse)

    # Check context data
    assert response.context_data["active_tab"] == title.lower()
    assert response.context_data["title"] == title
    assert response.context_data["new_object_url"] == reverse(new_url, args=[team_slug])
    assert response.context_data["table_url"] == reverse(table_url_name, args=[team_slug])
    assert response.context_data["enable_search"] is True
    assert response.context_data["toggle_archived"] is True


@pytest.mark.django_db()
def test_chatbot_experiment_table_view(client, team_with_users):
    team = team_with_users
    user = team.members.first()
    Experiment.objects.create(name="Test 1", pipeline=None, owner=user, team=team)
    Experiment.objects.create(
        name="Test 2",
        pipeline=Pipeline.objects.create(team=team, data={"nodes": [], "edges": []}),
        owner=user,
        team=team,
    )
    client.force_login(user)
    url = reverse("chatbots:table", args=[team.slug])
    response = client.get(url)

    assert response.status_code == 200
    assert "Test 2" in response.content.decode()
    assert "Test 1" not in response.content.decode()
