import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.template.response import TemplateResponse
from django.urls import reverse

from apps.experiments.models import Experiment
from apps.generics.views import generic_home
from apps.pipelines.models import Pipeline
from apps.teams.models import Team


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
def test_chatbot_experiment_table_view(client):
    User = get_user_model()
    user = User.objects.create_user(username="testuser", password="testpass")
    team = Team.objects.create(name="Test Team", slug="test-team")
    content_type = ContentType.objects.get_for_model(Experiment)
    permission = Permission.objects.get(codename="view_experiment", content_type=content_type)
    user.user_permissions.add(permission)
    Experiment.objects.create(name="Test 1", pipeline=None, owner=user, team=team)
    Experiment.objects.create(
        name="Test 2",
        pipeline=Pipeline.objects.create(team=team, data={"nodes": [], "edges": []}),
        owner=user,
        team=team,
    )
    client.login(username="testuser", password="testpass")
    url = reverse("chatbots:table", args=[team.slug])
    response = client.get(url)

    assert response.status_code == 200
    assert "Test 2" in response.content.decode()
    assert "Test 1" not in response.content.decode()
