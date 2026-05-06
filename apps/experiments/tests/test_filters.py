import json
from datetime import timedelta

import pytest
from django.http import QueryDict
from django.utils import timezone
from time_machine import travel

from apps.annotations.models import Tag, TagCategories
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.teams.models import Team
from apps.utils.deletion import delete_object_with_auditing_of_related_objects
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.web.dynamic_filters.base import Operators
from apps.web.dynamic_filters.datastructures import FilterParams


def _get_querydict(params: dict) -> QueryDict:
    query_dict = QueryDict("", mutable=True)
    query_dict.update(params)
    return query_dict


def _get_tag(team: Team, name: str, tag_category: TagCategories | None = None) -> Tag:
    tag, _ = Tag.objects.get_or_create(
        name=name,
        team=team,
        category=tag_category or TagCategories.BOT_RESPONSE,
    )
    return tag


@pytest.mark.django_db()
class TestExperimentSessionFilters:
    @pytest.fixture()
    def base_session(self):
        """Create a base experiment session with participant"""
        return ExperimentSessionFactory.create()

    @pytest.fixture()
    def sessions_with_tags(self):
        """Create sessions with different tag combinations"""
        session1 = ExperimentSessionFactory.create()
        session2 = ExperimentSessionFactory.create(experiment=session1.experiment)
        tag1 = _get_tag(team=session1.team, name="important")
        tag2 = _get_tag(team=session1.team, name="follow-up")

        session1.chat.add_tag(tag1, team=session1.team, added_by=None)
        session2.chat.add_tags([tag1, tag2], team=session2.team, added_by=None)
        return [session1, session2], [tag1, tag2]

    @pytest.fixture()
    def sessions_with_messages_tags(self):
        session1 = ExperimentSessionFactory.create()
        session2 = ExperimentSessionFactory.create(experiment=session1.experiment)

        tag1 = _get_tag(team=session1.team, name="important")
        tag2 = _get_tag(team=session1.team, name="follow-up")

        session1.chat.add_tag(tag1, team=session1.team, added_by=None)

        message = ChatMessage.objects.create(
            chat=session2.chat,
            content="Tagged message",
            message_type=ChatMessageType.HUMAN,
        )
        message.add_tag(tag1, team=session2.team, added_by=None)
        session2.chat.add_tag(tag2, team=session2.team, added_by=None)

        return [session1, session2], [tag1, tag2]

    @pytest.fixture()
    def sessions_with_versions(self):
        """Create sessions with different version tags on messages"""
        session1 = ExperimentSessionFactory.create()
        session2 = ExperimentSessionFactory.create(experiment=session1.experiment)

        v1_tag = _get_tag(team=session1.team, name="v1", tag_category=Chat.MetadataKeys.EXPERIMENT_VERSION)
        v2_tag = _get_tag(team=session1.team, name="v2", tag_category=Chat.MetadataKeys.EXPERIMENT_VERSION)

        msg1 = ChatMessage.objects.create(
            chat=session1.chat, content="Message with v1", message_type=ChatMessageType.HUMAN
        )
        msg1.add_tag(v1_tag, team=session1.team, added_by=None)
        session1.experiment_versions = [1]
        session1.save()

        msg2 = ChatMessage.objects.create(
            chat=session2.chat, content="Message with v1 and v2", message_type=ChatMessageType.HUMAN
        )
        msg2.add_tags([v1_tag, v2_tag], team=session1.team, added_by=None)
        session2.experiment_versions = [1, 2]
        session2.save()

        return [session1, session2], [v1_tag, v2_tag]

    @pytest.fixture()
    def sessions_with_statuses(self):
        """
        Create sessions with ACTIVE and COMPLETE status.
        """
        session1 = ExperimentSessionFactory.create(status=SessionStatus.ACTIVE)
        session2 = ExperimentSessionFactory.create(experiment=session1.experiment, status=SessionStatus.COMPLETE)

        return [session1, session2]

    @pytest.fixture()
    def session_with_many_message_tags(self):
        """One session whose chat contains multiple messages, each with multiple tags.

        A naive JOIN-based filter on `chat__messages__tags__name` returns the
        session N×M times (N matching messages × M matching tags) — a regression
        guard for the EXISTS rewrite.
        """
        session = ExperimentSessionFactory.create()
        team = session.team
        important = _get_tag(team=team, name="important")
        urgent = _get_tag(team=team, name="urgent")

        for content in ("first", "second", "third"):
            msg = ChatMessage.objects.create(
                chat=session.chat,
                content=content,
                message_type=ChatMessageType.HUMAN,
            )
            msg.add_tags([important, urgent], team=team, added_by=None)

        return session, [important, urgent]

    def test_message_tags_any_of_returns_no_duplicates(self, session_with_many_message_tags):
        session, _ = session_with_many_message_tags
        params = {
            "filter_0_column": "tags",
            "filter_0_operator": Operators.ANY_OF,
            "filter_0_value": json.dumps(["important", "urgent"]),
        }
        filtered = ExperimentSessionFilter().apply(
            session.experiment.sessions.all(), FilterParams(_get_querydict(params))
        )
        assert filtered.count() == 1
        assert list(filtered) == [session]

    def test_message_tags_all_of_returns_no_duplicates(self, session_with_many_message_tags):
        session, _ = session_with_many_message_tags
        params = {
            "filter_0_column": "tags",
            "filter_0_operator": Operators.ALL_OF,
            "filter_0_value": json.dumps(["important", "urgent"]),
        }
        filtered = ExperimentSessionFilter().apply(
            session.experiment.sessions.all(), FilterParams(_get_querydict(params))
        )
        assert filtered.count() == 1
        assert list(filtered) == [session]

    def test_message_tags_excludes_returns_no_duplicates(self, session_with_many_message_tags):
        """Exclude a tag that no message has — both sessions should remain, each exactly once.

        The bug being guarded against is `session` appearing 3 times (one per
        tagged message) when the global `.distinct()` is removed.
        """
        session, _ = session_with_many_message_tags
        other = ExperimentSessionFactory.create(experiment=session.experiment)

        params = {
            "filter_0_column": "tags",
            "filter_0_operator": Operators.EXCLUDES,
            "filter_0_value": json.dumps(["nonexistent"]),
        }
        filtered = ExperimentSessionFilter().apply(
            session.experiment.sessions.all(), FilterParams(_get_querydict(params))
        )
        assert filtered.count() == 2
        assert set(filtered) == {session, other}

    @pytest.mark.parametrize(
        ("operator", "value_offset_days", "should_match"),
        [
            (Operators.ON, 0, True),
            (Operators.ON, -1, False),
            (Operators.BEFORE, 1, True),
            (Operators.BEFORE, 0, False),
            (Operators.AFTER, -1, True),
            (Operators.AFTER, 0, False),
        ],
    )
    def test_message_date_filter_no_duplicates(
        self, session_with_many_message_tags, operator, value_offset_days, should_match
    ):
        """Every operator on the message-date filter must return one row per session,
        regardless of how many messages match. The naive JOIN through
        ``chat__messages__created_at`` would yield 3 rows for our fixture session.
        """
        session, _ = session_with_many_message_tags
        target_date = (timezone.now().date() + timedelta(days=value_offset_days)).isoformat()

        params = {
            "filter_0_column": "message_date",
            "filter_0_operator": operator,
            "filter_0_value": target_date,
        }
        filtered = ExperimentSessionFilter().apply(
            session.experiment.sessions.all(), FilterParams(_get_querydict(params))
        )
        if should_match:
            assert filtered.count() == 1
            assert list(filtered) == [session]
        else:
            assert filtered.count() == 0

    def test_message_date_filter_range_no_duplicates(self, session_with_many_message_tags):
        """The relative ``range`` operator must not multiply rows either."""
        session, _ = session_with_many_message_tags
        params = {
            "filter_0_column": "message_date",
            "filter_0_operator": Operators.RANGE,
            "filter_0_value": "1d",
        }
        filtered = ExperimentSessionFilter().apply(
            session.experiment.sessions.all(), FilterParams(_get_querydict(params))
        )
        assert filtered.count() == 1
        assert list(filtered) == [session]

    @travel("2025-01-03 10:00:00", tick=False)
    def test_message_timestamp_filters(self):
        """Test message timestamp filtering"""
        # Setup
        with travel("2025-01-01 10:00:00", tick=False):
            session1 = ExperimentSessionFactory.create()
            ChatMessage.objects.create(
                chat=session1.chat, content="First message for session 1", message_type=ChatMessageType.HUMAN
            )
            ChatMessage.objects.create(
                chat=session1.chat,
                content="Last message for session 1",
                message_type=ChatMessageType.HUMAN,
                created_at=timezone.now() + timedelta(hours=2),
            )
            session1.last_activity_at = timezone.now() - timedelta(hours=2)
            session1.save()

        with travel("2025-01-02 10:00:00", tick=False):
            session2 = ExperimentSessionFactory.create(experiment=session1.experiment)
            ChatMessage.objects.create(
                chat=session2.chat, content="First message for session 2", message_type=ChatMessageType.HUMAN
            )
            ChatMessage.objects.create(
                chat=session2.chat,
                content="Last message for session 2",
                message_type=ChatMessageType.HUMAN,
                created_at=timezone.now() + timedelta(hours=1),
            )
            session2.last_activity_at = timezone.now() - timedelta(hours=1)
            session2.save()

        # Test ON first message
        sessions_queryset = session1.experiment.sessions.all()
        params = {"filter_0_column": "first_message", "filter_0_operator": Operators.ON, "filter_0_value": "2025-01-01"}
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(sessions_queryset, FilterParams(_get_querydict(params)))
        assert filtered.count() == 1
        assert filtered.first() == session1

        # Test BEFORE last message
        params = {
            "filter_0_column": "last_message",
            "filter_0_operator": Operators.BEFORE,
            "filter_0_value": "2025-01-02",
        }
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(sessions_queryset, FilterParams(_get_querydict(params)))
        assert filtered.count() == 1
        assert filtered.first() == session1

        # Test AFTER first message
        params = {
            "filter_0_column": "first_message",
            "filter_0_operator": Operators.AFTER,
            "filter_0_value": "2025-01-01",
        }
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(sessions_queryset, FilterParams(_get_querydict(params)))
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
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert filtered.count() == 2

        # Test ANY_OF with multiple tags
        params["filter_0_value"] = json.dumps(["important", "follow-up"])
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert filtered.count() == 2

        # Test ALL_OF with multiple tags
        params["filter_0_operator"] = Operators.ALL_OF
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
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

        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert filtered.count() == 2

        # Test ALL_OF with both versions
        params["filter_0_operator"] = Operators.ALL_OF
        params["filter_0_value"] = json.dumps(["v1", "v2"])
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
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

        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert filtered.count() == 1
        assert filtered.first() == sessions[0]

    def test_empty_filters(self, base_session):
        """Test behavior with empty or invalid filters"""
        # Empty filter values should be ignored
        params = {"filter_0_column": "participant", "filter_0_operator": Operators.EQUALS, "filter_0_value": ""}

        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(
            base_session.experiment.sessions.all(), FilterParams(_get_querydict(params)), None
        )
        assert filtered.count() == base_session.experiment.sessions.count()

        # Invalid filter column should be ignored
        params["filter_0_column"] = "invalid_column"
        params["filter_0_value"] = "test"
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(
            base_session.experiment.sessions.all(), FilterParams(_get_querydict(params)), None
        )
        assert filtered.count() == base_session.experiment.sessions.count()

    def test_messages_tag_filters(self, sessions_with_messages_tags):
        """Test tag filtering with ANY_OF and ALL_OF operators"""
        sessions, tags = sessions_with_messages_tags

        # Test ANY_OF with one tag
        session_queryset = sessions[0].experiment.sessions.all()
        params = {
            "filter_0_column": "tags",
            "filter_0_operator": Operators.ANY_OF,
            "filter_0_value": json.dumps(["important"]),
        }

        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert set(filtered) == set(sessions), f"Expected both sessions with 'important', got {filtered}"

        params["filter_0_value"] = json.dumps(["follow-up"])
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert set(filtered) == {sessions[1]}, f"Expected session2 with 'follow-up', got {filtered}"

        params["filter_0_value"] = json.dumps(["important", "follow-up"])
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert set(filtered) == set(sessions), f"Expected both sessions with either tag, got {filtered}"

        params["filter_0_operator"] = Operators.ALL_OF
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert list(filtered) == [sessions[1]], f"Expected only session2 with both tags, got {list(filtered)}"

        params["filter_0_operator"] = Operators.EXCLUDES
        params["filter_0_value"] = json.dumps(["important"])
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert list(filtered) == [], f"Expected no sessions to exclude 'important', got {list(filtered)}"

    def test_state_filters(self, sessions_with_statuses):
        """
        Test status filter with ANY_OF and EXCLUDES.
        """
        sessions = sessions_with_statuses
        session_queryset = sessions[0].experiment.sessions.all()

        params = {
            "filter_0_column": "state",
            "filter_0_operator": Operators.ANY_OF,
            "filter_0_value": json.dumps([SessionStatus.ACTIVE.value]),
        }

        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert all(s.status == SessionStatus.ACTIVE for s in filtered)

        params["filter_0_operator"] = Operators.EXCLUDES
        params["filter_0_value"] = json.dumps([SessionStatus.ACTIVE.value])
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert all(s.status != SessionStatus.ACTIVE for s in filtered)

    def test_remote_id_filters(self, sessions_with_statuses):
        sessions = sessions_with_statuses
        session_queryset = sessions[0].experiment.sessions.all()

        test_id = "test-remote-id-123"

        session_to_update = session_queryset.first()
        session_to_update.participant.remote_id = test_id
        session_to_update.participant.save()

        params = {
            "filter_0_column": "remote_id",
            "filter_0_operator": Operators.ANY_OF,
            "filter_0_value": json.dumps([test_id]),
        }

        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert all(s.participant.remote_id == test_id for s in filtered)

        params["filter_0_operator"] = Operators.EXCLUDES
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert all(s.participant.remote_id != test_id for s in filtered)

    def test_session_id_filters(self, sessions_with_statuses):
        sessions = sessions_with_statuses
        session_queryset = sessions[0].experiment.sessions.all()
        target = sessions[0]
        target_external_id = str(target.external_id)

        params = {
            "filter_0_column": "session_id",
            "filter_0_operator": Operators.EQUALS,
            "filter_0_value": target_external_id,
        }
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert list(filtered) == [target]

        params["filter_0_operator"] = Operators.EQUALS
        params["filter_0_value"] = target_external_id.upper()
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert list(filtered) == [target], "EQUALS should be case-insensitive"

        params["filter_0_operator"] = Operators.STARTS_WITH
        params["filter_0_value"] = target_external_id[:8]
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert target in filtered

        params["filter_0_operator"] = Operators.DOES_NOT_CONTAIN
        params["filter_0_value"] = target_external_id
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(session_queryset, FilterParams(_get_querydict(params)))
        assert target not in filtered


@pytest.mark.django_db()
class TestParticipantFilter:
    @pytest.fixture(scope="class")
    def session(self, django_db_setup, django_db_blocker):
        """Create a base experiment session with participant"""
        with django_db_blocker.unblock():
            team = TeamFactory.create()
            session = ExperimentSessionFactory.create(
                team=team, participant__name="Jeremy Fisher", participant__identifier="test.user@example.com"
            )
            yield session
            delete_object_with_auditing_of_related_objects(team)

    @pytest.mark.parametrize(
        ("operator", "value", "count"),
        [
            (Operators.EQUALS, "something else", 0),
            (Operators.EQUALS, "test.user@example.com", 1),
            (Operators.EQUALS, "TEST.user@example.com", 0),  # case-sensitive
            (Operators.CONTAINS, "user@", 1),
            (Operators.CONTAINS, "user1@", 0),
            (Operators.DOES_NOT_CONTAIN, "user1@", 1),
            (Operators.DOES_NOT_CONTAIN, "user@", 0),
            (Operators.STARTS_WITH, "test", 1),
            (Operators.STARTS_WITH, "tester", 0),
            (Operators.ENDS_WITH, "example.com", 1),
            (Operators.ENDS_WITH, "domain.com", 0),
            (Operators.ANY_OF, json.dumps(["test.user@example.com", "another@example.com"]), 1),
            (Operators.ANY_OF, json.dumps(["tester@example.com", "another@example.com"]), 0),
            # test matching name
            (Operators.EQUALS, "Jeremy Fisher", 1),
            (Operators.CONTAINS, "remy", 1),
            (Operators.DOES_NOT_CONTAIN, "Ptolemy", 1),
            (Operators.STARTS_WITH, "jeremy", 1),
            (Operators.ENDS_WITH, "fisher", 1),
            (Operators.ANY_OF, json.dumps(["Jeremy Fisher", "Sir Ptolemy Tortoise"]), 1),
            (Operators.ANY_OF, json.dumps(["jeremy fisher"]), 0),  # case-sensitive
        ],
    )
    def test_participant_filters(self, session, operator, value, count):
        """Test all participant filter operators"""

        params = _get_querydict(
            {
                "filter_0_column": "participant",
                "filter_0_operator": operator,
                "filter_0_value": value,
            }
        )

        queryset = session.experiment.sessions.all()
        session_filter = ExperimentSessionFilter()
        filtered = session_filter.apply(queryset, FilterParams(params))
        assert filtered.count() == count
        if count == 1:
            assert filtered.first() == session


@pytest.mark.django_db()
def test_session_filter_default_query_does_not_join_experiment_channel():
    """No filter, no display column references experiment_channel — confirm the COUNT
    does not pull it in unnecessarily."""

    team = TeamFactory.create()
    qs = ExperimentSession.objects.get_table_queryset(team)
    qs = ExperimentSessionFilter().apply(qs, FilterParams())
    sql = str(qs.values("id").query).lower()
    assert "channels_experimentchannel" not in sql, sql
