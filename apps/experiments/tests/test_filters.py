import json
from datetime import timedelta

import pytest
from django.utils import timezone
from freezegun import freeze_time

from apps.annotations.models import Tag, TagCategories
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.filters import Operators, apply_dynamic_filters
from apps.teams.models import Team
from apps.utils.factories.experiment import ExperimentSessionFactory


def _get_tag(team: Team, name: str, tag_category: TagCategories | None = None) -> Tag:
    tag, _ = Tag.objects.get_or_create(
        name=name,
        team=team,
        category=tag_category or TagCategories.BOT_RESPONSE,
    )
    return tag


@pytest.mark.django_db()
class TestDynamicFilters:
    @pytest.fixture()
    def base_session(self):
        """Create a base experiment session with participant"""
        return ExperimentSessionFactory()

    @pytest.fixture()
    def sessions_with_tags(self):
        """Create sessions with different tag combinations"""
        session1 = ExperimentSessionFactory()
        session2 = ExperimentSessionFactory(experiment=session1.experiment)
        tag1 = _get_tag(team=session1.team, name="important")
        tag2 = _get_tag(team=session1.team, name="follow-up")

        session1.chat.add_tag(tag1, team=session1.team, added_by=None)
        session2.chat.add_tags([tag1, tag2], team=session2.team, added_by=None)
        return [session1, session2], [tag1, tag2]

    @pytest.fixture()
    def sessions_with_versions(self):
        """Create sessions with different version tags on messages"""
        session1 = ExperimentSessionFactory()
        session2 = ExperimentSessionFactory(experiment=session1.experiment)

        v1_tag = _get_tag(team=session1.team, name="v1", tag_category=Chat.MetadataKeys.EXPERIMENT_VERSION)
        v2_tag = _get_tag(team=session1.team, name="v2", tag_category=Chat.MetadataKeys.EXPERIMENT_VERSION)

        msg1 = ChatMessage.objects.create(
            chat=session1.chat, content="Message with v1", message_type=ChatMessageType.HUMAN
        )
        msg1.add_tag(v1_tag, team=session1.team, added_by=None)

        msg2 = ChatMessage.objects.create(
            chat=session2.chat, content="Message with v1 and v2", message_type=ChatMessageType.HUMAN
        )
        msg2.add_tags([v1_tag, v2_tag], team=session1.team, added_by=None)

        return [session1, session2], [v1_tag, v2_tag]

    def test_participant_filters(self, base_session):
        """Test all participant filter operators"""
        session = base_session
        session.participant.identifier = "test.user@example.com"
        session.participant.save()

        # Test EQUALS
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.EQUALS,
            "filter_0_value": "test.user@example.com",
        }
        filtered = apply_dynamic_filters(session.experiment.sessions.all(), None, params)
        assert filtered.count() == 1
        assert filtered.first() == session

        # Test CONTAINS
        params["filter_0_operator"] = Operators.CONTAINS
        params["filter_0_value"] = "user@"
        filtered = apply_dynamic_filters(session.experiment.sessions.all(), None, params)
        assert filtered.count() == 1

        # Test DOES_NOT_CONTAIN
        params["filter_0_operator"] = Operators.DOES_NOT_CONTAIN
        params["filter_0_value"] = "nonexistent"
        filtered = apply_dynamic_filters(session.experiment.sessions.all(), None, params)
        assert filtered.count() == 1

        # Test STARTS_WITH
        params["filter_0_operator"] = Operators.STARTS_WITH
        params["filter_0_value"] = "test"
        filtered = apply_dynamic_filters(session.experiment.sessions.all(), None, params)
        assert filtered.count() == 1

        # Test ENDS_WITH
        params["filter_0_operator"] = Operators.ENDS_WITH
        params["filter_0_value"] = "@example.com"
        filtered = apply_dynamic_filters(session.experiment.sessions.all(), None, params)
        assert filtered.count() == 1

    @freeze_time("2025-01-03 10:00:00")
    def test_message_timestamp_filters(self):
        """Test message timestamp filtering"""
        # Setup
        with freeze_time("2025-01-01 10:00:00"):
            session1 = ExperimentSessionFactory()
            ChatMessage.objects.create(
                chat=session1.chat, content="First message for session 1", message_type=ChatMessageType.HUMAN
            )
            ChatMessage.objects.create(
                chat=session1.chat,
                content="Last message for session 1",
                message_type=ChatMessageType.HUMAN,
                created_at=timezone.now() + timedelta(hours=2),
            )

        with freeze_time("2025-01-02 10:00:00"):
            session2 = ExperimentSessionFactory(experiment=session1.experiment)
            ChatMessage.objects.create(
                chat=session2.chat, content="First message for session 2", message_type=ChatMessageType.HUMAN
            )
            ChatMessage.objects.create(
                chat=session2.chat,
                content="Last message for session 2",
                message_type=ChatMessageType.HUMAN,
                created_at=timezone.now() + timedelta(hours=1),
            )

        # Test
        # Test ON first message
        sessions_queryset = session1.experiment.sessions.all()
        params = {"filter_0_column": "first_message", "filter_0_operator": Operators.ON, "filter_0_value": "2025-01-01"}
        filtered = apply_dynamic_filters(sessions_queryset, None, params)
        assert filtered.count() == 1
        assert filtered.first() == session1

        # Test BEFORE last message
        params = {
            "filter_0_column": "last_message",
            "filter_0_operator": Operators.BEFORE,
            "filter_0_value": "2025-01-02",
        }
        filtered = apply_dynamic_filters(sessions_queryset, None, params)
        assert filtered.count() == 1
        assert filtered.first() == session1

        # Test AFTER first message
        params = {
            "filter_0_column": "first_message",
            "filter_0_operator": Operators.AFTER,
            "filter_0_value": "2025-01-01",
        }
        filtered = apply_dynamic_filters(sessions_queryset, None, params)
        assert filtered.count() == 1
        assert filtered.first() == session2

    def test_tag_filters(self, sessions_with_tags):
        """Test tag filtering with ANY_OF and ALL_OF operators"""
        sessions, tags = sessions_with_tags

        # Test ANY_OF with one tag
        session_queryset = sessions[0].experiment.sessions.all()
        params = {
            "filter_0_column": "tags",
            "filter_0_operator": Operators.ANY_OF,
            "filter_0_value": json.dumps(["important"]),
        }
        filtered = apply_dynamic_filters(session_queryset, None, params)
        assert filtered.count() == 2

        # Test ANY_OF with multiple tags
        params["filter_0_value"] = json.dumps(["important", "follow-up"])
        filtered = apply_dynamic_filters(session_queryset, None, params)
        assert filtered.count() == 2

        # Test ALL_OF with multiple tags
        params["filter_0_operator"] = Operators.ALL_OF
        filtered = apply_dynamic_filters(session_queryset, None, params)
        assert filtered.count() == 1
        assert filtered.first() == sessions[1]

    def test_version_filters(self, sessions_with_versions):
        """Test version tag filtering"""
        sessions, version_tags = sessions_with_versions

        # Test ANY_OF with one version
        session_queryset = sessions[0].experiment.sessions.all()
        params = {
            "filter_0_column": "versions",
            "filter_0_operator": Operators.ANY_OF,
            "filter_0_value": json.dumps(["v1"]),
        }
        filtered = apply_dynamic_filters(session_queryset, None, params)
        assert filtered.count() == 2

        # Test ALL_OF with both versions
        params["filter_0_operator"] = Operators.ALL_OF
        params["filter_0_value"] = json.dumps(["v1", "v2"])
        filtered = apply_dynamic_filters(session_queryset, None, params)
        assert filtered.count() == 1
        assert filtered.first() == sessions[1]

    def test_multiple_filters(self, sessions_with_tags):
        """Test combining multiple filters"""
        sessions, tags = sessions_with_tags
        sessions[0].participant.identifier = "user1@example.com"
        sessions[0].participant.save()
        sessions[1].participant.identifier = "user2@example.com"
        sessions[1].participant.save()

        session_queryset = sessions[0].experiment.sessions.all()
        params = {
            "filter_0_column": "participant",
            "filter_0_operator": Operators.CONTAINS,
            "filter_0_value": "user1",
            "filter_1_column": "tags",
            "filter_1_operator": Operators.ANY_OF,
            "filter_1_value": json.dumps(["important"]),
        }
        filtered = apply_dynamic_filters(session_queryset, None, params)
        assert filtered.count() == 1
        assert filtered.first() == sessions[0]

    def test_empty_filters(self, base_session):
        """Test behavior with empty or invalid filters"""
        # Empty filter values should be ignored
        params = {"filter_0_column": "participant", "filter_0_operator": Operators.EQUALS, "filter_0_value": ""}
        filtered = apply_dynamic_filters(base_session.experiment.sessions.all(), None, params)
        assert filtered.count() == base_session.experiment.sessions.count()

        # Invalid filter column should be ignored
        params["filter_0_column"] = "invalid_column"
        params["filter_0_value"] = "test"
        filtered = apply_dynamic_filters(base_session.experiment.sessions.all(), None, params)
        assert filtered.count() == base_session.experiment.sessions.count()
