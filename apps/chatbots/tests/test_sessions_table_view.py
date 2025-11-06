"""
Tests for ChatbotSessionsTableView optimization.

These tests verify that the view correctly defers expensive annotations until after
pagination counting to improve query performance.
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.urls import reverse

from apps.annotations.models import CustomTaggedItem, Tag
from apps.chat.models import Chat, ChatMessage
from apps.chatbots.tables import ChatbotSessionsTable
from apps.chatbots.views import ChatbotSessionsTableView
from apps.teams.helpers import get_team_membership_for_request
from apps.utils.factories.experiment import ChatbotFactory, ExperimentSessionFactory, ParticipantFactory
from apps.utils.factories.team import TeamWithUsersFactory


def attach_session_middleware_to_request(request):
    """Helper to add session support to test requests."""
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()


@pytest.fixture()
def chatbot_with_sessions(db):
    """Create a chatbot with multiple sessions containing messages and tags."""
    team_with_users = TeamWithUsersFactory.create()
    user = team_with_users.members.first()
    chatbot = ChatbotFactory(team=team_with_users, owner=user)

    # Create sessions with varying numbers of messages
    sessions = []
    for i in range(5):
        session = ExperimentSessionFactory(
            experiment=chatbot, team=team_with_users, participant=ParticipantFactory(team=team_with_users)
        )

        # Add messages to each session
        for j in range(i + 1):  # Session 0 has 1 message, session 1 has 2, etc.
            message = ChatMessage.objects.create(
                chat=session.chat, message_type="ai", content=f"Message {j} for session {i}"
            )

            # Add version tags to some messages
            if j == 0:
                message_ct = ContentType.objects.get_for_model(ChatMessage)
                tag, _ = Tag.objects.get_or_create(
                    name=f"v{i + 1}", category=Chat.MetadataKeys.EXPERIMENT_VERSION, team=team_with_users
                )
                CustomTaggedItem.objects.create(
                    content_type=message_ct, object_id=message.id, tag=tag, team=team_with_users
                )

        sessions.append(session)

    return {"chatbot": chatbot, "team": team_with_users, "user": user, "sessions": sessions}


@pytest.mark.django_db()
class TestChatbotSessionsTableView:
    """Test suite for ChatbotSessionsTableView optimization."""

    def test_view_renders_successfully(self, chatbot_with_sessions):
        """Test that the view renders without errors."""
        data = chatbot_with_sessions
        team = data["team"]
        user = data["user"]
        chatbot = data["chatbot"]

        factory = RequestFactory()
        request = factory.get(
            reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": chatbot.id})
        )
        request.user = user
        request.team = team
        request.team_membership = get_team_membership_for_request(request)
        attach_session_middleware_to_request(request)

        view = ChatbotSessionsTableView.as_view()
        response = view(request, team_slug=team.slug, experiment_id=chatbot.id)

        assert response.status_code == 200
        assert isinstance(response.context_data["table"], ChatbotSessionsTable)

    def test_table_data_includes_message_count_annotation(self, chatbot_with_sessions):
        """Test that table data includes the message_count annotation."""
        data = chatbot_with_sessions
        team = data["team"]
        user = data["user"]
        chatbot = data["chatbot"]
        sessions = data["sessions"]

        factory = RequestFactory()
        request = factory.get(
            reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": chatbot.id})
        )
        request.user = user
        request.team = team
        request.team_membership = get_team_membership_for_request(request)
        attach_session_middleware_to_request(request)

        view = ChatbotSessionsTableView()
        view.request = request
        view.kwargs = {"team_slug": team.slug, "experiment_id": chatbot.id}

        # Get table data which should include annotations
        table_data = list(view.get_table_data())

        # Verify all sessions are returned
        assert len(table_data) == len(sessions)

        # Verify message_count annotation is present and correct
        # Create a mapping of session IDs to their expected message counts
        session_message_counts = {}
        for i, session in enumerate(sessions):
            session_message_counts[session.id] = i + 1  # Session i has i+1 messages

        for session in table_data:
            assert hasattr(session, "message_count")
            assert session.message_count is not None
            assert session.message_count == session_message_counts[session.id]

    def test_table_data_includes_last_message_annotation(self, chatbot_with_sessions):
        """Test that table data includes the last_message_created_at annotation."""
        data = chatbot_with_sessions
        team = data["team"]
        user = data["user"]
        chatbot = data["chatbot"]

        factory = RequestFactory()
        request = factory.get(
            reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": chatbot.id})
        )
        request.user = user
        request.team = team
        request.team_membership = get_team_membership_for_request(request)
        attach_session_middleware_to_request(request)

        view = ChatbotSessionsTableView()
        view.request = request
        view.kwargs = {"team_slug": team.slug, "experiment_id": chatbot.id}

        table_data = list(view.get_table_data())

        # Verify last_message_created_at annotation is present
        for session in table_data:
            assert hasattr(session, "last_message_created_at")
            assert session.last_message_created_at is not None

    def test_table_data_includes_experiment_versions_annotation(self, chatbot_with_sessions):
        """Test that table data includes the experiment_versions annotation."""
        data = chatbot_with_sessions
        team = data["team"]
        user = data["user"]
        chatbot = data["chatbot"]
        sessions = data["sessions"]

        factory = RequestFactory()
        request = factory.get(
            reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": chatbot.id})
        )
        request.user = user
        request.team = team
        request.team_membership = get_team_membership_for_request(request)
        attach_session_middleware_to_request(request)

        view = ChatbotSessionsTableView()
        view.request = request
        view.kwargs = {"team_slug": team.slug, "experiment_id": chatbot.id}

        table_data = list(view.get_table_data())

        # Create a mapping of session IDs to their expected version tags
        session_versions = {}
        for i, session in enumerate(sessions):
            session_versions[session.id] = f"v{i + 1}"

        # Verify experiment_versions annotation is present
        for session in table_data:
            assert hasattr(session, "experiment_versions")
            expected_version = session_versions[session.id]
            assert expected_version in session.experiment_versions

    def test_queryset_does_not_include_most_expensive_annotations(self, chatbot_with_sessions):
        """Test that the base queryset doesn't include the most expensive annotations.

        Note: The filter adds last/first_message_created_at for date filtering,
        but we avoid the expensive experiment_versions and message_count annotations
        until get_table_data() is called.
        """
        data = chatbot_with_sessions
        team = data["team"]
        user = data["user"]
        chatbot = data["chatbot"]

        factory = RequestFactory()
        request = factory.get(
            reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": chatbot.id})
        )
        request.user = user
        request.team = team
        request.team_membership = get_team_membership_for_request(request)
        attach_session_middleware_to_request(request)

        view = ChatbotSessionsTableView()
        view.request = request
        view.kwargs = {"team_slug": team.slug, "experiment_id": chatbot.id}

        # Get the base queryset (used for counting)
        queryset = view.get_queryset()

        # The most expensive annotations should not be present yet
        query_annotations = queryset.query.annotations
        # experiment_versions is the slowest annotation - it should not be in the base queryset
        assert "experiment_versions" not in query_annotations
        # message_count should also not be in the base queryset
        assert "message_count" not in query_annotations
        # Note: last_message_created_at may be present due to filter requirements, which is acceptable

    def test_pagination_works_with_annotations(self, chatbot_with_sessions):
        """Test that pagination works correctly with deferred annotations."""
        data = chatbot_with_sessions
        team = data["team"]
        user = data["user"]
        chatbot = data["chatbot"]

        # Request with pagination (page size 2)
        factory = RequestFactory()
        request = factory.get(
            reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": chatbot.id}),
            {"per_page": "2"},
        )
        request.user = user
        request.team = team
        request.team_membership = get_team_membership_for_request(request)
        attach_session_middleware_to_request(request)

        view = ChatbotSessionsTableView.as_view()
        response = view(request, team_slug=team.slug, experiment_id=chatbot.id)

        assert response.status_code == 200
        table = response.context_data["table"]

        # Verify pagination is active
        assert table.paginator is not None
        assert table.paginator.num_pages > 1

        # Verify the displayed rows have annotations
        # Access the actual record from BoundRow using row.record
        for row in table.page:
            record = row.record
            assert hasattr(record, "message_count")
            assert hasattr(record, "last_message_created_at")
            assert hasattr(record, "experiment_versions")

    def test_empty_chatbot_renders_correctly(self, db):
        """Test that a chatbot with no sessions renders correctly."""
        team_with_users = TeamWithUsersFactory.create()
        user = team_with_users.members.first()
        chatbot = ChatbotFactory(team=team_with_users, owner=user)

        factory = RequestFactory()
        request = factory.get(
            reverse("chatbots:sessions-list", kwargs={"team_slug": team_with_users.slug, "experiment_id": chatbot.id})
        )
        request.user = user
        request.team = team_with_users
        request.team_membership = get_team_membership_for_request(request)
        attach_session_middleware_to_request(request)

        view = ChatbotSessionsTableView.as_view()
        response = view(request, team_slug=team_with_users.slug, experiment_id=chatbot.id)

        assert response.status_code == 200
        assert isinstance(response.context_data["table"], ChatbotSessionsTable)
        assert len(response.context_data["table"].data) == 0

    def test_session_without_messages_has_null_annotations(self, db):
        """Test that sessions without messages have appropriate null values for annotations."""
        team_with_users = TeamWithUsersFactory.create()
        user = team_with_users.members.first()
        chatbot = ChatbotFactory(team=team_with_users, owner=user)

        # Create a session with no messages
        ExperimentSessionFactory(
            experiment=chatbot, team=team_with_users, participant=ParticipantFactory(team=team_with_users)
        )

        factory = RequestFactory()
        request = factory.get(
            reverse("chatbots:sessions-list", kwargs={"team_slug": team_with_users.slug, "experiment_id": chatbot.id})
        )
        request.user = user
        request.team = team_with_users
        request.team_membership = get_team_membership_for_request(request)
        attach_session_middleware_to_request(request)

        view = ChatbotSessionsTableView()
        view.request = request
        view.kwargs = {"team_slug": team_with_users.slug, "experiment_id": chatbot.id}

        table_data = list(view.get_table_data())

        assert len(table_data) == 1
        session_data = table_data[0]

        # Session with no messages should have null/0 values
        assert session_data.message_count is None or session_data.message_count == 0
        assert session_data.last_message_created_at is None
        assert session_data.experiment_versions == ""

    def test_chatbot_column_hidden_for_single_chatbot_view(self, chatbot_with_sessions):
        """Test that the chatbot column is hidden when viewing a specific chatbot's sessions."""
        data = chatbot_with_sessions
        team = data["team"]
        user = data["user"]
        chatbot = data["chatbot"]

        factory = RequestFactory()
        request = factory.get(
            reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": chatbot.id})
        )
        request.user = user
        request.team = team
        request.team_membership = get_team_membership_for_request(request)
        attach_session_middleware_to_request(request)

        view = ChatbotSessionsTableView()
        view.request = request
        view.kwargs = {"team_slug": team.slug, "experiment_id": chatbot.id}

        table = view.get_table()

        # The chatbot column should be excluded when viewing a specific chatbot
        assert "chatbot" in table.exclude
