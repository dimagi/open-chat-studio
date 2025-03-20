import pytest
from django.contrib.auth.models import Permission
from django.template.response import TemplateResponse
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.chatbots.views import BaseChatbotView
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

    assert isinstance(response, TemplateResponse)

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
    client.login(username="testuser", password="testpass")
    url = reverse("chatbots:table", args=[team.slug])
    response = client.get(url)

    assert response.status_code == 200
    assert "Test 2" in response.content.decode()
    assert "Test 1" not in response.content.decode()


@pytest.mark.django_db()
def test_base_chatbot_view_success_url(team_with_users):
    """Test that BaseChatbotView returns the correct success URL."""
    team = team_with_users
    user = team.members.first()
    pipeline = Pipeline.objects.create(team=team, name="Test Pipeline", data={"nodes": [], "edges": []})
    experiment = Experiment.objects.create(
        name="Test Experiment", description="Test Description", owner=user, team=team, pipeline=pipeline
    )
    factory = RequestFactory()
    request = factory.get(reverse("chatbots:edit", args=[team.slug, pipeline.id]))
    request.team = team
    request.user = user

    view = BaseChatbotView()
    view.request = request
    view.object = experiment

    expected_url = reverse("chatbots:edit", args=[team.slug, pipeline.id])
    assert view.get_success_url() == expected_url


@pytest.mark.django_db()
def test_create_chatbot_view(team_with_users):
    team = team_with_users
    user = team.members.first()
    client = Client()
    client.force_login(user)

    url = reverse("chatbots:new", args=[team.slug])
    data = {
        "name": "My Chatbot",
        "description": "This is a chatbot.",
    }
    response = client.post(url, data)

    assert Experiment.objects.filter(name="My Chatbot", team=team).exists()
    experiment = Experiment.objects.get(name="My Chatbot")
    assert experiment.pipeline is not None
    expected_url = reverse("chatbots:edit", args=[team.slug, experiment.pipeline.id])
    assert response.status_code == 302
    assert response.url == expected_url


@pytest.mark.django_db()
def test_single_chatbot_home(client, team_with_users):
    team = team_with_users
    user = team.members.first()
    user.user_permissions.add(Permission.objects.get(codename="view_experiment"))
    client.force_login(user)
    pipeline = Pipeline.objects.create(team=team, name="Test Pipeline", data={"nodes": [], "edges": []})
    experiment = Experiment.objects.create(
        name="Test Experiment", description="Test Description", owner=user, team=team, pipeline=pipeline
    )

    url = reverse("chatbots:single_chatbot_home", args=[team.slug, experiment.id])
    response = client.get(url)

    assert response.status_code == 200
    assert "chatbots/single_chatbot_home.html" in [t.name for t in response.templates]
