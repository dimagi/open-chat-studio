import re
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth.models import Group, Permission
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connection
from django.template.response import TemplateResponse
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.html import escape

from apps.annotations.models import Tag
from apps.api.session_tokens import validate_session_token
from apps.chat.models import Chat
from apps.chatbots.tables import ChatbotSessionsTable
from apps.chatbots.views import (
    ChatbotExperimentTableView,
    ChatbotSessionsTableView,
    ChatbotVersionsTableView,
    CreateChatbotVersion,
    chatbot_session_pagination_view,
    home,
)
from apps.events.models import StaticTriggerType
from apps.experiments.models import Experiment, ExperimentSession, Participant, SessionStatus
from apps.pipelines.models import Pipeline
from apps.teams.backends import CHAT_VIEWER_GROUP, add_user_to_team, create_default_groups
from apps.teams.helpers import get_team_membership_for_request
from apps.teams.utils import set_current_team
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.user import UserFactory


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
    content = response.content.decode()
    assert "Test 2" in content
    assert "Test 1" not in content


@pytest.mark.django_db()
def test_chatbot_experiment_table_queryset_has_no_select_distinct(team_with_users):
    """The chatbot list queryset must not emit ``SELECT DISTINCT`` — its filters and
    annotations don't introduce row multiplication, and a deduping outer SELECT would
    defeat the indexed plan. ``COUNT(DISTINCT ...)`` inside an aggregate subquery is a
    different concern and is allowed."""
    team = team_with_users
    user = team.members.first()

    factory = RequestFactory()
    request = factory.get(reverse("chatbots:table", args=[team.slug]))
    request.user = user
    request.team = team
    request.team_membership = get_team_membership_for_request(request)
    attach_session_middleware_to_request(request)
    set_current_team(team)

    view = ChatbotExperimentTableView()
    view.request = request
    view.kwargs = {"team_slug": team.slug}
    sql = str(view.get_queryset().query).lower()
    assert "select distinct" not in sql, sql


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
def test_single_chatbot_home_version_snapshot_redirects_to_working_version(client, team_with_users):
    team = team_with_users
    user = team.members.first()
    user.user_permissions.add(Permission.objects.get(codename="view_experiment"))
    client.force_login(user)
    pipeline = Pipeline.objects.create(team=team, name="Test Pipeline", data={"nodes": [], "edges": []})
    experiment = Experiment.objects.create(
        name="Test Experiment", description="Test Description", owner=user, team=team, pipeline=pipeline
    )
    snapshot = experiment.create_new_version()

    url = reverse("chatbots:single_chatbot_home", args=[team.slug, snapshot.id])
    response = client.get(url)

    expected_url = reverse("chatbots:single_chatbot_home", args=[team.slug, experiment.id])
    assert response.status_code == 302
    assert response["Location"] == f"{expected_url}?version_id={snapshot.version_number}#versions"


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


@pytest.mark.django_db()
def test_versions_table_chat_action_opens_widget(client, team_with_users):
    """The per-version chat button launches the embedded widget pinned to that version number."""
    team = team_with_users
    user = team.members.first()
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team, owner=user, file_uploads_enabled=True)
    version = experiment.create_new_version()

    url = reverse("chatbots:versions-list", kwargs={"team_slug": team.slug, "experiment_id": experiment.id})
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()
    assert f"openChatWidget({version.version_number}, " in content
    assert f"headerText: 'Version {version.version_number}'" in content
    assert "allowAttachments: true" in content


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
    set_current_team(team)

    view = ChatbotSessionsTableView.as_view()
    response = view(request, team_slug=team.slug, experiment_id=experiment.id)
    assert response.status_code == 200
    assert isinstance(response.context_data["table"], ChatbotSessionsTable)


@pytest.mark.django_db()
def test_chatbot_sessions_table_view_applies_both_filters_on_one_column(client, team_with_users):
    """A date range built from two filters on the same column must exclude out-of-range sessions.

    Regression test for filters on the same column overwriting each other, which let
    sessions from outside the requested range through.
    """
    team = team_with_users
    user = team.members.first()
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team)
    in_range, out_of_range = (
        ExperimentSessionFactory.create(
            team=team,
            experiment=experiment,
            participant__team=team,
            first_activity_at=datetime(2026, month, 15, tzinfo=UTC),
        )
        for month in (5, 1)
    )

    url = reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": experiment.id})
    # A date range is two filters on one column, expressed as repeated f_/op_ keys (the Django
    # test client encodes list values with doseq=True, producing f_first_message twice).
    response = client.get(
        url,
        {
            "f_first_message": ["2026-04-30", "2026-06-01"],
            "op_first_message": ["after", "before"],
        },
    )

    assert response.status_code == 200
    assert list(response.context_data["table"].data.data) == [in_range]
    assert str(out_of_range.external_id) not in response.content.decode()


@pytest.mark.django_db()
def test_continue_chat_action_opens_widget(client, team_with_users):
    """The Continue Chat action opens the session in the embedded widget."""
    team = team_with_users
    user = team.members.first()
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team)
    session = ExperimentSessionFactory.create(
        team=team,
        experiment=experiment,
        participant__team=team,
        participant__user=user,
        status=SessionStatus.ACTIVE,
    )

    url = reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": experiment.id})
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()

    assert "ocsContinueSessionChat(this)" in content
    assert f'data-session-id="{session.external_id}"' in content
    token = re.search(r'data-session-token="([^"]+)"', content).group(1)
    assert validate_session_token(token, session.external_id)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("session_version", "expected_label"),
    [
        pytest.param(0, "Published Version", id="published-alias"),
        pytest.param(2, "Working Version (v2)", id="working-version"),
        pytest.param(1, "Version 1", id="older-version"),
    ],
)
def test_continue_chat_action_labels_the_session_version(session_version, expected_label, client, team_with_users):
    """The widget's version badge must not call an older snapshot the working version."""
    team = team_with_users
    user = team.members.first()
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team)
    experiment.create_new_version()
    experiment.refresh_from_db()
    assert experiment.version_number == 2, "working version should have moved on after snapshotting v1"

    session = ExperimentSessionFactory.create(
        team=team,
        experiment=experiment,
        participant__team=team,
        participant__user=user,
        status=SessionStatus.ACTIVE,
    )
    session.chat.set_metadata(Chat.MetadataKeys.EXPERIMENT_VERSION, session_version)

    url = reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": experiment.id})
    content = client.get(url).content.decode()

    assert f'data-version-label="{expected_label}"' in content


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("session_version", "expected_attachments"),
    [
        pytest.param(0, "true", id="published-alias-follows-snapshot"),
        pytest.param(1, "true", id="older-version-follows-snapshot"),
        pytest.param(2, "false", id="working-version-follows-working-row"),
    ],
)
def test_continue_chat_action_uses_the_session_versions_attachment_setting(
    session_version, expected_attachments, client, team_with_users
):
    """``file_uploads_enabled`` is versioned, so the working row can disagree with the snapshot the
    session is chatting to. The widget must follow the version the session actually targets."""
    team = team_with_users
    user = team.members.first()
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team, file_uploads_enabled=True)
    experiment.create_new_version(make_default=True)
    # Working version diverges after publishing v1.
    experiment.file_uploads_enabled = False
    experiment.save()
    experiment.refresh_from_db()
    assert experiment.version_number == 2, "working version should have moved on after snapshotting v1"

    session = ExperimentSessionFactory.create(
        team=team,
        experiment=experiment,
        participant__team=team,
        participant__user=user,
        status=SessionStatus.ACTIVE,
    )
    session.chat.set_metadata(Chat.MetadataKeys.EXPERIMENT_VERSION, session_version)

    url = reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": experiment.id})
    content = client.get(url).content.decode()

    assert f'data-allow-attachments="{expected_attachments}"' in content


@pytest.mark.django_db()
def test_continue_chat_action_falls_back_when_the_session_version_is_archived(client, team_with_users):
    """An archived version is invisible to the default manager, so resolving it raises. The button
    still has to render — fall back to the working row's setting."""
    team = team_with_users
    user = team.members.first()
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team, file_uploads_enabled=True)
    version = experiment.create_new_version()
    experiment.refresh_from_db()

    session = ExperimentSessionFactory.create(
        team=team,
        experiment=experiment,
        participant__team=team,
        participant__user=user,
        status=SessionStatus.ACTIVE,
    )
    session.chat.set_metadata(Chat.MetadataKeys.EXPERIMENT_VERSION, version.version_number)
    version.is_archived = True
    version.save()

    url = reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": experiment.id})
    response = client.get(url)

    assert response.status_code == 200
    assert 'data-allow-attachments="true"' in response.content.decode()


@pytest.mark.django_db()
def test_single_chatbot_home_renders_chat_widget(client, team_with_users):
    """The chat dropdown launches the embedded widget."""
    team = team_with_users
    user = team.members.first()
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team, owner=user)

    url = reverse("chatbots:single_chatbot_home", args=[team.slug, experiment.id])
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()
    assert f'chatbot-id="{experiment.public_id}"' in content
    assert f"openChatWidget({experiment.version_number}, " in content
    assert "openChatWidget(0, " in content


@pytest.mark.django_db()
def test_single_chatbot_home_has_no_web_channel_entry_points(client, team_with_users):
    """The web channel was removed: no public share link and no participant invitations."""
    team = team_with_users
    user = team.members.first()
    user.user_permissions.add(Permission.objects.get(codename="invite_participants"))
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team, owner=user)

    url = reverse("chatbots:single_chatbot_home", args=[team.slug, experiment.id])
    content = client.get(url).content.decode()

    assert "sharing_modal" not in content
    assert "Invitations" not in content


@pytest.mark.django_db()
def test_published_version_launcher_uses_the_published_versions_settings(client, team_with_users):
    """``file_uploads_enabled`` is versioned, so the published snapshot can disagree with the
    working row. The version-0 launcher must follow the snapshot it actually chats to."""
    team = team_with_users
    user = team.members.first()
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team, owner=user, file_uploads_enabled=True)
    experiment.create_new_version(make_default=True)
    # Working version diverges after publishing.
    experiment.file_uploads_enabled = False
    experiment.save()
    experiment.refresh_from_db()

    url = reverse("chatbots:single_chatbot_home", args=[team.slug, experiment.id])
    content = client.get(url).content.decode()

    assert "openChatWidget(0, {allowAttachments: true })" in content
    assert f"openChatWidget({experiment.version_number}, {{allowAttachments: false }})" in content


@pytest.mark.django_db()
@pytest.mark.parametrize("fire_end_event", [True, False])
@patch("apps.events.tasks.enqueue_static_triggers")
def test_end_chatbot_session_view(enqueue_static_triggers_task, fire_end_event, client, team_with_users):
    team = team_with_users
    user = team.members.first()
    client.force_login(user)

    session = ExperimentSessionFactory.create(
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
        enqueue_static_triggers_task.delay.assert_called_once_with(
            session.id, StaticTriggerType.CONVERSATION_END_MANUALLY
        )
    else:
        enqueue_static_triggers_task.delay.assert_not_called()


@pytest.mark.django_db()
@pytest.mark.parametrize(("fire_end_event", "prompt"), [(True, "Start with this"), (False, ""), (False, None)])
@patch("apps.events.tasks.enqueue_static_triggers")
@patch("apps.channels.channel_base.ChannelBase.start_new_session")
@patch("apps.chatbots.views.send_bot_message.delay")
def test_new_chatbot_session_view(
    task_mock, mock_start_new_session, enqueue_static_triggers_task, fire_end_event, prompt, client, team_with_users
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

    old_session = ExperimentSessionFactory.create(
        participant__identifier="participant@example.com",
        team=team,
        status=SessionStatus.ACTIVE,
    )

    new_session = ExperimentSessionFactory.create(
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
    task_mock.assert_called_once()
    call_args = task_mock.call_args
    expected_prompt = prompt if prompt else ""
    assert call_args[1]["instruction_prompt"] == expected_prompt

    # Verify event was fired if requested
    if fire_end_event:
        enqueue_static_triggers_task.delay.assert_called_once_with(
            old_session.id, StaticTriggerType.CONVERSATION_END_MANUALLY
        )
    else:
        enqueue_static_triggers_task.delay.assert_not_called()


@pytest.mark.django_db()
def test_disallow_web_channel_session_resets(team_with_users, client):
    team = team_with_users
    user = team.members.first()
    client.force_login(user)

    session = ExperimentSessionFactory.create(
        participant__identifier="participant@example.com",
        experiment_channel__platform="web",
        team=team,
        status=SessionStatus.ACTIVE,
    )

    url = reverse(
        "chatbots:chatbot_new_session",
        args=[team.slug, session.experiment.public_id, session.external_id],
    )
    response = client.post(url, {})
    assert response.status_code == 302
    session.refresh_from_db()
    assert session.status == SessionStatus.ACTIVE  # Session should remain active


@pytest.mark.django_db()
def test_chatbot_table_view_embeds_trend_data_inline(client, team_with_users):
    """The chatbot table embeds trend JSON directly in the HTML instead of generating per-experiment fetch URLs."""
    team = team_with_users
    user = team.members.first()
    Pipeline.objects.create(team=team, data={"nodes": [], "edges": []})
    Experiment.objects.create(
        name="Test Chatbot",
        pipeline=Pipeline.objects.create(team=team, data={"nodes": [], "edges": []}),
        owner=user,
        team=team,
    )

    client.force_login(user)
    url = reverse("chatbots:table", args=[team.slug])
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()
    # Inline data attribute should be present, not a per-experiment fetch URL
    assert "data-trends=" in content
    assert "trends_data" not in content


@pytest.mark.django_db()
def test_last_activity_annotation_shows_most_recent_non_null(team_with_users):
    """The last_activity annotation must return the most recent non-null last_activity_at.

    PostgreSQL ORDER BY col DESC defaults to NULLS FIRST, so without an explicit
    NULLS LAST directive the subquery picks a NULL session instead of the most
    recently-active one, causing the Last Activity column to appear blank even
    when real activity exists.
    """

    team = team_with_users
    user = team.members.first()
    pipeline = Pipeline.objects.create(team=team, data={"nodes": [], "edges": []})
    experiment = Experiment.objects.create(name="Test Chatbot", owner=user, team=team, pipeline=pipeline)

    # A bot-initiated session that never received a human message → last_activity_at is null
    ExperimentSessionFactory.create(experiment=experiment, last_activity_at=None)

    # A session with genuine user activity
    activity_time = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    ExperimentSessionFactory.create(experiment=experiment, last_activity_at=activity_time)

    request = RequestFactory().get(reverse("chatbots:table", args=[team.slug]))
    request.team = team
    request.user = user
    request.GET = request.GET.copy()

    view = ChatbotExperimentTableView()
    view.request = request
    view.kwargs = {}
    view.args = []

    result = view.get_queryset().get(id=experiment.id)
    assert result.last_activity == activity_time


@pytest.mark.django_db()
def test_chatbot_admin_can_access_edit_chatbot(client, team_with_users):
    """Regression test: users with the Chatbot Admin group must be able to
    access the EditChatbot view (requires experiments.change_experiment)."""

    team = team_with_users
    chatbot_admin_group = Group.objects.get(name="Chatbot Admin")

    user = UserFactory.create()
    membership = MembershipFactory.create(team=team, user=user)
    membership.groups.add(chatbot_admin_group)

    pipeline = Pipeline.objects.create(team=team, data={"nodes": [], "edges": []})
    experiment = Experiment.objects.create(name="Test Chatbot", owner=user, team=team, pipeline=pipeline)

    client.force_login(user)
    url = reverse("chatbots:edit", args=[team.slug, experiment.id])
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_session_table_prefetch_is_page_bounded(team_with_users):
    """Adding more sessions than fit on one page must not increase the number of
    queries fired for the tag prefetch — the prefetch should target only the
    sessions visible on the current page."""
    team = team_with_users
    user = team.members.first()
    experiment = ExperimentFactory.create(team=team)

    # Two sessions, both tagged. Page size is 25 by default; both fit on one page.
    tag = Tag.objects.create(team=team, name="t")
    for _ in range(2):
        s = ExperimentSessionFactory.create(team=team, experiment=experiment)
        s.chat.add_tag(tag, team=team, added_by=None)

    factory = RequestFactory()

    def do_request():
        request = factory.get(
            reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": experiment.id})
        )
        request.user = user
        request.team = team
        request.team_membership = get_team_membership_for_request(request)
        attach_session_middleware_to_request(request)
        set_current_team(team)
        view = ChatbotSessionsTableView.as_view()
        response = view(request, team_slug=team.slug, experiment_id=experiment.id)
        # Force evaluation of the table data
        list(response.context_data["table"].rows)
        return response

    with CaptureQueriesContext(connection) as ctx_two:
        do_request()
    queries_for_two = len(ctx_two.captured_queries)

    # Add another 30 tagged sessions — far beyond one page.
    for _ in range(30):
        s = ExperimentSessionFactory.create(team=team, experiment=experiment)
        s.chat.add_tag(tag, team=team, added_by=None)

    with CaptureQueriesContext(connection) as ctx_many:
        do_request()
    queries_for_many = len(ctx_many.captured_queries)

    # Adding off-page rows must not bloat the prefetch. Slack absorbs minor non-prefetch
    # variation (e.g. an extra permission/feature-flag fetch) without missing a real
    # page-boundedness regression — those would 10×+ the count, not change it by 1–2.
    allowed_slack = 2
    assert queries_for_many <= queries_for_two + allowed_slack, (
        f"Prefetch is not page-bounded: 2 sessions = {queries_for_two} queries, "
        f"32 sessions = {queries_for_many} queries (slack {allowed_slack})"
    )


@pytest.mark.django_db()
def test_session_table_session_query_uses_limit(team_with_users):
    """The session list page must fetch sessions with LIMIT — not materialise the entire
    filtered queryset before pagination. Detects the case where the tag-prefetch helper is
    attached at an unpaginated lifecycle hook (e.g. ``get_table_data`` instead of after
    ``RequestConfig.configure``)."""
    team = team_with_users
    user = team.members.first()
    experiment = ExperimentFactory.create(team=team)

    tag = Tag.objects.create(team=team, name="t")
    # 50 sessions — twice the default page size.
    for _ in range(50):
        s = ExperimentSessionFactory.create(team=team, experiment=experiment)
        s.chat.add_tag(tag, team=team, added_by=None)

    factory = RequestFactory()
    request = factory.get(
        reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": experiment.id})
    )
    request.user = user
    request.team = team
    request.team_membership = get_team_membership_for_request(request)
    attach_session_middleware_to_request(request)
    set_current_team(team)

    with CaptureQueriesContext(connection) as ctx:
        view = ChatbotSessionsTableView.as_view()
        response = view(request, team_slug=team.slug, experiment_id=experiment.id)
        # `paginated_rows` is what the django-tables2 template iterates in production.
        # Iterating `table.rows` would force an unpaginated SELECT that the template never fires.
        list(response.context_data["table"].paginated_rows)

    # Exclude the paginator's `SELECT COUNT(*)` row by matching its prefix; do NOT use
    # `"count(" not in sql` because the main paginated SELECT embeds a `Subquery(... Count("id") ...)`
    # for the message_count annotation, which would falsely match and leave `session_selects` empty
    # (making the `all(...)` assertion below vacuously true).
    session_selects = [
        q["sql"]
        for q in ctx.captured_queries
        if "experiments_experimentsession" in q["sql"].lower()
        and not q["sql"].lstrip().lower().startswith("select count(")
    ]
    assert session_selects, (
        "Expected at least one row-fetching SELECT on experiments_experimentsession but found none.\n"
        "All captured SQL:\n" + "\n\n".join(q["sql"] for q in ctx.captured_queries)
    )
    assert all("limit" in sql.lower() for sql in session_selects), (
        "Session list ran an unbounded SELECT on experiments_experimentsession (no LIMIT) — "
        "pagination is not being applied at the SQL level. Session selects captured:\n" + "\n\n".join(session_selects)
    )


@pytest.mark.django_db()
def test_version_operation_status_polling(client, team_with_users):
    """The status endpoint reports any in-flight version operation, not just publish."""
    team = team_with_users
    user = team.members.first()
    user.user_permissions.add(Permission.objects.get(codename="view_experiment"))
    client.force_login(user)
    experiment = ExperimentFactory.create(team=team, owner=user)
    url = reverse("chatbots:check_version_operation_status", args=[team.slug, experiment.id])

    # lock held by some non-publish operation: response keeps polling
    experiment.acquire_version_operation_lock("revert-task-id")
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "Creating Version" in content
    assert url in content  # hx-get poll target

    # operation finished: response renders the create button and triggers a refresh
    Experiment.release_version_operation_lock(experiment.id)
    response = client.get(url)
    content = response.content.decode()
    assert "Create Version" in content
    assert "version-changed" in content


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("embed_source", "expect_link"),
    [
        pytest.param("https://embedder.example.com/page", True, id="https-is-a-link"),
        pytest.param("javascript:alert(document.domain)", False, id="javascript-is-text"),
        pytest.param("data:text/html,<script>alert(1)</script>", False, id="data-is-text"),
    ],
)
def test_session_view_only_links_http_embed_source(client, team_with_users, embed_source, expect_link):
    """The embed source is captured from an unauthenticated referer header, so the session page
    must never render it as a link target unless it is an http(s) URL."""
    team = team_with_users
    user = team.members.first()
    session = ExperimentSessionFactory.create(experiment__team=team)
    session.chat.set_metadata(Chat.MetadataKeys.EMBED_SOURCE, embed_source)
    client.force_login(user)

    url = reverse(
        "chatbots:chatbot_session_view",
        args=[team.slug, session.experiment.public_id, session.external_id],
    )
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()

    hrefs = re.findall(r'href="([^"]*)"', content)
    assert not [href for href in hrefs if href.lower().startswith(("javascript:", "data:"))]
    if expect_link:
        assert embed_source in hrefs
    else:
        assert embed_source not in hrefs
        assert escape(embed_source) in content  # still displayed, as inert text


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "view_name",
    [
        pytest.param("chatbots:chatbot_session_view", id="session-detail"),
        pytest.param("experiments:experiment_session_messages_view", id="session-messages"),
    ],
)
@pytest.mark.parametrize(
    ("identity", "expected_status"),
    [
        pytest.param("chat_viewer", 200, id="team-member-with-chat-view-chat"),
        pytest.param("no_perm_member", 403, id="team-member-without-chat-view-chat"),
        pytest.param("non_member", 404, id="authenticated-non-member"),
        pytest.param("anonymous", 302, id="anonymous-redirects-to-login"),
    ],
)
def test_session_transcript_views_require_team_membership(client, view_name, identity, expected_status):
    """Session transcripts are team-internal.

    The public chat is gone, so there is no participant-facing path into these views any more:
    access is team membership plus `chat.view_chat`, and the participant-owns-the-session
    bypass (which used to serve the removed chat UI) no longer applies.
    """
    create_default_groups()
    team = TeamFactory.create()
    session = ExperimentSessionFactory.create(experiment__team=team)

    if identity == "chat_viewer":
        user = UserFactory.create()
        add_user_to_team(team, user, groups=[CHAT_VIEWER_GROUP])
        client.force_login(user)
    elif identity == "no_perm_member":
        user = UserFactory.create()
        add_user_to_team(team, user, groups=[])
        client.force_login(user)
    elif identity == "non_member":
        # A participant who owns the session but has no team membership: the old
        # access check let this through, team auth does not.
        user = UserFactory.create()
        session.participant.user = user
        session.participant.save()
        client.force_login(user)

    url = reverse(view_name, args=[team.slug, session.experiment.public_id, session.external_id])
    assert client.get(url).status_code == expected_status
