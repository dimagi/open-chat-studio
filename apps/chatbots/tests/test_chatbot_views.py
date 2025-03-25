import pytest
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.template.response import TemplateResponse
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.chatbots.tables import ChatbotSessionsTable
from apps.chatbots.views import (
    BaseChatbotView,
    ChatbotSessionsTableView,
    ChatbotVersionsTableView,
    CreateChatbotVersion,
    chatbot_session_pagination_view,
)
from apps.experiments.models import Experiment, ExperimentSession, Participant
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


@pytest.mark.django_db()
def test_get_success_url(team_with_users):
    team = team_with_users
    user = team.members.first()
    pipeline = None
    experiment = Experiment.objects.create(
        name="Test Experiment", description="Test Description", owner=user, team=team, pipeline=pipeline
    )
    factory = RequestFactory()
    request = factory.get(
        reverse("chatbots:create_version", kwargs={"team_slug": team.slug, "experiment_id": experiment.id})
    )
    request.user = user
    request.team = team

    view = CreateChatbotVersion()
    view.request = request
    view.kwargs = {"experiment_id": experiment.id}

    success_url = view.get_success_url()
    expected_url = f"{reverse('chatbots:single_chatbot_home', kwargs={'team_slug': team.slug, 'experiment_id': experiment.id})}#versions"
    assert success_url == expected_url


@pytest.mark.django_db()
def test_chatbot_versions_table_view(team_with_users):
    team = team_with_users
    user = team.members.first()
    experiment = Experiment.objects.create(name="Chatbot Experiment", description="Description", owner=user, team=team)
    factory = RequestFactory()
    url = reverse("chatbots:versions-list", kwargs={"team_slug": team.slug, "experiment_id": experiment.id})
    request = factory.get(url)
    request.user = user
    request.team = team
    view = ChatbotVersionsTableView()
    view.request = request
    view.kwargs = {"experiment_id": experiment.id}

    response = view.get(request)

    assert response.status_code == 200
    assert view.template_name == "experiments/experiment_version_table.html"
    assert "table" in response.context_data
    assert isinstance(response.context_data["table"], view.table_class)
    table = response.context_data["table"]
    assert len(table.data) == 1
    assert table.data[0] == experiment


@pytest.mark.django_db()
def test_chatbot_session_pagination_view(team_with_users):
    team = team_with_users
    user = team.members.first()
    experiment = Experiment.objects.create(
        name="Test Experiment",
        description="Test description",
        owner=user,
        team=team,
    )
    participant = Participant.objects.create(user=user, team=team)
    session_1 = ExperimentSession.objects.create(
        experiment=experiment,
        participant=participant,
        external_id="session1",
        created_at="2025-03-01T10:00:00Z",
        team=team,
    )
    session_2 = ExperimentSession.objects.create(
        experiment=experiment,
        participant=participant,
        external_id="session2",
        created_at="2025-03-01T10:05:00Z",
        team=team,
    )
    session_3 = ExperimentSession.objects.create(
        experiment=experiment,
        participant=participant,
        external_id="session3",
        created_at="2025-03-01T10:10:00Z",
        team=team,
    )

    factory = RequestFactory()

    request_next = factory.get(
        reverse(
            "chatbots:chatbot_session_pagination_view",
            kwargs={"team_slug": team.slug, "experiment_id": experiment.id, "session_id": session_1.external_id},
        ),
        {"dir": "next"},
    )
    request_next.user = user
    request_next.team = team
    request_next.experiment_session = session_1
    request_next.experiment = experiment
    response_next = chatbot_session_pagination_view(
        request_next, team_slug=team.slug, experiment_id=experiment.id, session_id=session_1.external_id
    )

    assert response_next.status_code == 302
    assert response_next.url == reverse(
        "chatbots:chatbot_session_view",
        kwargs={"team_slug": team.slug, "experiment_id": experiment.id, "session_id": session_2.external_id},
    )
    request_prev = factory.get(
        reverse(
            "chatbots:chatbot_session_pagination_view",
            kwargs={"team_slug": team.slug, "experiment_id": experiment.id, "session_id": session_2.external_id},
        ),
        {"dir": "previous"},
    )
    request_prev.user = user
    request_prev.team = team
    request_prev.experiment_session = session_2
    request_prev.experiment = experiment
    response_prev = chatbot_session_pagination_view(
        request_prev, team_slug=team.slug, experiment_id=experiment.id, session_id=session_2.external_id
    )
    assert response_prev.status_code == 302
    assert response_prev.url == reverse(
        "chatbots:chatbot_session_view",
        kwargs={"team_slug": team.slug, "experiment_id": experiment.id, "session_id": session_1.external_id},
    )
    request_no_next = factory.get(
        reverse(
            "chatbots:chatbot_session_pagination_view",
            kwargs={"team_slug": team.slug, "experiment_id": experiment.id, "session_id": session_3.external_id},
        ),
        {"dir": "next"},
    )
    request_no_next.user = user
    request_no_next.team = team
    request_no_next.experiment_session = session_3
    request_no_next.experiment = experiment

    response_no_next = chatbot_session_pagination_view(
        request_no_next, team_slug=team.slug, experiment_id=experiment.id, session_id=session_3.external_id
    )
    messages = list(get_messages(request_no_next))
    assert len(messages) == 1
    assert str(messages[0]) == "No more sessions to paginate"
    assert response_no_next.status_code == 302
    assert response_no_next.url == reverse(
        "chatbots:chatbot_session_view",
        kwargs={"team_slug": team.slug, "experiment_id": experiment.id, "session_id": session_3.external_id},
    )
    request_no_prev = factory.get(
        reverse(
            "chatbots:chatbot_session_pagination_view",
            kwargs={"team_slug": team.slug, "experiment_id": experiment.id, "session_id": session_1.external_id},
        ),
        {"dir": "previous"},
    )
    request_no_prev.user = user
    request_no_prev.team = team
    request_no_prev.experiment_session = session_1
    request_no_prev.experiment = experiment
    response_no_prev = chatbot_session_pagination_view(
        request_no_prev, team_slug=team.slug, experiment_id=experiment.id, session_id=session_1.external_id
    )
    messages = list(get_messages(request_no_prev))
    assert len(messages) == 1
    assert str(messages[0]) == "No more sessions to paginate"
    assert response_no_prev.status_code == 302
    assert response_no_prev.url == reverse(
        "chatbots:chatbot_session_view",
        kwargs={"team_slug": team.slug, "experiment_id": experiment.id, "session_id": session_1.external_id},
    )


@pytest.mark.django_db()
def test_chatbot_sessions_table_view(team_with_users):
    # Setup
    team = team_with_users
    user = team.members.first()

    experiment = Experiment.objects.create(
        name="Test Experiment",
        description="Test description",
        owner=user,
        team=team,
    )

    session = ExperimentSession.objects.create(
        experiment=experiment,
        external_id="session1",
        created_at="2025-03-01T10:00:00Z",
        team=team,
    )

    factory = RequestFactory()
    request = factory.get(
        reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": experiment.id})
    )
    request.user = user
    request.team = team

    # Test view class
    view = ChatbotSessionsTableView.as_view()
    response = view(request, team_slug=team.slug, experiment_id=experiment.id)
    assert response.status_code == 200
    assert isinstance(response.context_data["table"], ChatbotSessionsTable)
