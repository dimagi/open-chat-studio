from unittest.mock import Mock, patch

import pytest
from django.contrib.auth.models import Permission
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.template.response import TemplateResponse
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.chatbots.tables import ChatbotSessionsTable
from apps.chatbots.views import (
    ChatbotSessionsTableView,
    ChatbotVersionsTableView,
    CreateChatbotVersion,
    chatbot_session_pagination_view,
    home,
)
from apps.events.models import StaticTriggerType
from apps.experiments.models import Experiment, ExperimentSession, Participant, SessionStatus
from apps.pipelines.models import Pipeline
from apps.teams.helpers import get_team_membership_for_request
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_chatbot_home():
    team_slug = "test-team"
    title = "Chatbots"
    table_url_name = "chatbots:table"
    actions = [{"action": "chatbots:new"}]
    response = home(None, team_slug, title, table_url_name, actions=actions)

    assert isinstance(response, TemplateResponse)

    assert response.context_data["active_tab"] == title.lower()
    assert response.context_data["title"] == title
    assert response.context_data["table_url"] == reverse(table_url_name, args=[team_slug])
    assert response.context_data["enable_search"] is True
    assert response.context_data["toggle_archived"] is True
    assert response.context_data["actions"] == actions


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
    expected_url = reverse("chatbots:edit", args=[team.slug, experiment.id])
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
    url = "chatbots:single_chatbot_home"
    expected_url = f"{reverse(url, kwargs={'team_slug': team.slug, 'experiment_id': experiment.id})}#versions"
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


def attach_session_middleware_to_request(request):
    session_middleware = SessionMiddleware(lambda req: None)
    session_middleware.process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)


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
            kwargs={"team_slug": team.slug, "experiment_id": experiment.public_id, "session_id": session_1.external_id},
        ),
        {"dir": "next"},
    )
    request_next.user = user
    request_next.team = team
    request_next.team_membership = get_team_membership_for_request(request_next)
    request_next.experiment_session = session_1
    request_next.experiment = experiment
    attach_session_middleware_to_request(request_next)
    response_next = chatbot_session_pagination_view(
        request_next, team_slug=team.slug, experiment_id=experiment.public_id, session_id=session_1.external_id
    )
    assert response_next.status_code == 302
    assert response_next["Location"] == reverse(
        "chatbots:chatbot_session_view",
        kwargs={"team_slug": team.slug, "experiment_id": experiment.public_id, "session_id": session_2.external_id},
    )
    request_prev = factory.get(
        reverse(
            "chatbots:chatbot_session_pagination_view",
            kwargs={"team_slug": team.slug, "experiment_id": experiment.public_id, "session_id": session_2.external_id},
        ),
        {"dir": "previous"},
    )
    request_prev.user = user
    request_prev.team = team
    request_prev.team_membership = get_team_membership_for_request(request_prev)
    request_prev.experiment_session = session_2
    request_prev.experiment = experiment
    attach_session_middleware_to_request(request_prev)
    response_prev = chatbot_session_pagination_view(
        request_prev, team_slug=team.slug, experiment_id=experiment.public_id, session_id=session_2.external_id
    )
    assert response_prev.status_code == 302
    assert response_prev["Location"] == reverse(
        "chatbots:chatbot_session_view",
        kwargs={"team_slug": team.slug, "experiment_id": experiment.public_id, "session_id": session_1.external_id},
    )
    request_no_next = factory.get(
        reverse(
            "chatbots:chatbot_session_pagination_view",
            kwargs={"team_slug": team.slug, "experiment_id": experiment.public_id, "session_id": session_3.external_id},
        ),
        {"dir": "next"},
    )
    request_no_next.user = user
    request_no_next.team = team
    request_no_next.team_membership = get_team_membership_for_request(request_no_next)
    request_no_next.experiment_session = session_3
    request_no_next.experiment = experiment
    attach_session_middleware_to_request(request_no_next)
    response_no_next = chatbot_session_pagination_view(
        request_no_next, team_slug=team.slug, experiment_id=experiment.public_id, session_id=session_3.external_id
    )
    assert response_no_next.status_code == 302
    assert response_no_next["Location"] == reverse(
        "chatbots:chatbot_session_view",
        kwargs={"team_slug": team.slug, "experiment_id": experiment.public_id, "session_id": session_3.external_id},
    )
    request_no_prev = factory.get(
        reverse(
            "chatbots:chatbot_session_pagination_view",
            kwargs={"team_slug": team.slug, "experiment_id": experiment.public_id, "session_id": session_1.external_id},
        ),
        {"dir": "previous"},
    )
    request_no_prev.user = user
    request_no_prev.team = team
    request_no_prev.team_membership = get_team_membership_for_request(request_no_prev)
    request_no_prev.experiment_session = session_1
    request_no_prev.experiment = experiment
    attach_session_middleware_to_request(request_no_prev)
    response_no_prev = chatbot_session_pagination_view(
        request_no_prev, team_slug=team.slug, experiment_id=experiment.public_id, session_id=session_1.external_id
    )
    assert response_no_prev.status_code == 302
    assert response_no_prev["Location"] == reverse(
        "chatbots:chatbot_session_view",
        kwargs={"team_slug": team.slug, "experiment_id": experiment.public_id, "session_id": session_1.external_id},
    )


@pytest.mark.django_db()
def test_chatbot_sessions_table_view(team_with_users):
    team = team_with_users
    user = team.members.first()

    experiment = Experiment.objects.create(
        name="Test Experiment",
        description="Test description",
        owner=user,
        team=team,
    )

    factory = RequestFactory()
    request = factory.get(
        reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": experiment.id})
    )
    request.user = user
    request.team = team
    request.team_membership = get_team_membership_for_request(request)
    attach_session_middleware_to_request(request)

    view = ChatbotSessionsTableView.as_view()
    response = view(request, team_slug=team.slug, experiment_id=experiment.id)
    assert response.status_code == 200
    assert isinstance(response.context_data["table"], ChatbotSessionsTable)


@pytest.mark.django_db()
@pytest.mark.parametrize("fire_end_event", [True, False])
@patch("apps.events.tasks.enqueue_static_triggers")
def test_end_chatbot_session_view(enqueue_static_triggers_task, fire_end_event, client, team_with_users):
    team = team_with_users
    user = team.members.first()
    client.force_login(user)

    session = ExperimentSessionFactory(
        participant__identifier="participant@example.com",
        participant__platform="web",
        team=team,
        status=SessionStatus.ACTIVE,
    )

    url = reverse(
        "chatbots:chatbot_end_session",
        args=[team.slug, session.experiment.public_id, session.external_id],
    )
    post_data = {}
    if fire_end_event:
        post_data["fire_end_event"] = "on"
    response = client.post(url, post_data)

    assert response.status_code == 302
    session.refresh_from_db()
    assert session.status == SessionStatus.PENDING_REVIEW
    assert session.ended_at is not None
    if fire_end_event:
        enqueue_static_triggers_task.delay.assert_called_once_with(session.id, StaticTriggerType.CONVERSATION_END)
    else:
        enqueue_static_triggers_task.delay.assert_not_called()


@pytest.mark.django_db()
@pytest.mark.parametrize(("fire_end_event", "prompt"), [(True, "Start with this"), (False, ""), (False, None)])
@patch("apps.events.tasks.enqueue_static_triggers")
@patch("apps.chat.channels.ChannelBase.start_new_session")
def test_new_chatbot_session_view(
    mock_start_new_session, enqueue_static_triggers_task, fire_end_event, prompt, client, team_with_users
):
    """Test that new_chatbot_session creates a new session, ends the old one, and sends ad-hoc message.

    Covers:
    - Ending old session with/without propagating end event
    - Creating new session using same channel and participant
    - Sending ad-hoc message with/without prompt
    - Redirecting to new session view
    """
    team = team_with_users
    user = team.members.first()
    client.force_login(user)

    old_session = ExperimentSessionFactory(
        participant__identifier="participant@example.com",
        participant__platform="web",
        team=team,
        status=SessionStatus.ACTIVE,
    )

    new_session = ExperimentSessionFactory(
        participant=old_session.participant,
        experiment=old_session.experiment,
        experiment_channel=old_session.experiment_channel,
        team=team,
        status=SessionStatus.ACTIVE,
        external_id="new_session_id",
    )

    new_session.ad_hoc_bot_message = Mock()
    mock_start_new_session.return_value = new_session

    url = reverse(
        "chatbots:chatbot_new_session",
        args=[team.slug, old_session.experiment.public_id, old_session.external_id],
    )
    post_data = {}
    if fire_end_event:
        post_data["fire_end_event"] = "on"
    if prompt is not None:
        post_data["prompt"] = prompt

    response = client.post(url, post_data)
    assert response.status_code == 302

    # Verify the old session was ended
    old_session.refresh_from_db()
    assert old_session.status == SessionStatus.PENDING_REVIEW
    assert old_session.ended_at is not None

    # Verify start_new_session was called with correct parameters
    mock_start_new_session.assert_called_once()
    call_kwargs = mock_start_new_session.call_args[1]
    assert call_kwargs["working_experiment"] == old_session.experiment
    assert call_kwargs["experiment_channel"] == old_session.experiment_channel
    assert call_kwargs["participant_identifier"] == old_session.participant.identifier
    assert call_kwargs["participant_user"] == old_session.participant.user
    assert call_kwargs["session_status"] == SessionStatus.ACTIVE

    # Verify ad_hoc_bot_message was called with correct prompt
    new_session.ad_hoc_bot_message.assert_called_once()
    call_args = new_session.ad_hoc_bot_message.call_args
    expected_prompt = prompt if prompt else ""
    assert call_args[1]["instruction_prompt"] == expected_prompt

    # Verify event was fired if requested
    if fire_end_event:
        enqueue_static_triggers_task.delay.assert_called_once_with(old_session.id, StaticTriggerType.CONVERSATION_END)
    else:
        enqueue_static_triggers_task.delay.assert_not_called()
