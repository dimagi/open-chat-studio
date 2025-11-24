"""
Tests for duplicate prevention in DashboardService.

These tests verify that dashboard service querysets prevent row duplication from JOINs,
which would lead to duplicate counting in aggregations and queryset results.

The service uses multiple strategies to prevent duplicates:
- Exists() subqueries to avoid JOINs (e.g., message filtering, platform filtering)
- distinct() calls where necessary (e.g., participant filtering with multiple sessions)
- distinct=True in Count() aggregations to prevent over-counting

Run these tests to verify the service correctly handles duplicate prevention across various
filtering scenarios including date ranges, tags, platforms, and participant filters.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.annotations.models import Tag
from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import (
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
)
from apps.utils.factories.team import TeamFactory

from ..services import DashboardService


@pytest.mark.django_db()
class TestDistinctSessionDuplication:
    """Test cases for session duplication"""

    def test_sessions_not_duplicated_with_multiple_messages_in_date_range(self):
        """
        TEST: Sessions duplicated when filtering by message date range.
        """
        team = TeamFactory()
        experiment = ExperimentFactory(team=team)
        participant = ParticipantFactory(team=team)
        session = ExperimentSessionFactory(
            experiment=experiment,
            participant=participant,
            team=team,
        )

        # Create 3 messages in the date range for the same session
        now = timezone.now()
        for i in range(3):
            ChatMessage.objects.create(
                chat=session.chat,
                message_type=ChatMessageType.HUMAN,
                content=f"Message {i}",
                created_at=now - timedelta(hours=i),
            )

        service = DashboardService(team)
        start_date = now - timedelta(days=1)
        end_date = now + timedelta(hours=1)

        querysets = service.get_filtered_queryset_base(start_date=start_date, end_date=end_date)
        sessions = querysets["sessions"]

        # Count actual sessions (list() forces evaluation and joins happen)
        session_list = list(sessions)
        session_ids = [s.id for s in session_list]

        # This assertion verifies the fix is working - expecting 1, getting 3
        assert len(session_list) == 1, (
            f"Session duplicated! Expected 1 session, got {len(session_list)}. "
            f"Session IDs: {session_ids}. "
            f"This indicates .distinct() is missing from the sessions queryset."
        )

    def test_experiments_not_duplicated_with_platform_filter(self):
        """
        TEST: Experiments duplicated when filtering by platform.
        """
        team = TeamFactory()
        experiment = ExperimentFactory(team=team)
        participant = ParticipantFactory(team=team)

        # Create 2 channels with the same platform for the same experiment
        platform = ChannelPlatform.TELEGRAM
        channel1 = ExperimentChannelFactory(
            team=team,
            experiment=experiment,
            platform=platform,
            name="Channel 1",
        )
        ExperimentChannelFactory(
            team=team,
            experiment=experiment,
            platform=platform,
            name="Channel 2",
        )

        # Create session with one of the channels
        ExperimentSessionFactory(
            experiment=experiment,
            participant=participant,
            team=team,
            experiment_channel=channel1,
        )

        service = DashboardService(team)

        querysets = service.get_filtered_queryset_base(platform_names=[platform])
        experiments = querysets["experiments"]

        # Count actual experiments
        experiment_list = list(experiments)
        experiment_ids = [e.id for e in experiment_list]

        # This assertion verifies the fix is working - expecting 1, getting 2+
        assert len(experiment_list) == 1, (
            f"Experiment duplicated! Expected 1 experiment, got {len(experiment_list)}. "
            f"Experiment IDs: {experiment_ids}. "
            f"This indicates .distinct() is missing after experimentchannel filter."
        )

    def test_participants_not_duplicated_with_multiple_sessions(self):
        """
        TEST: Participants duplicated when filtering by participant_ids.
        """
        team = TeamFactory()
        experiment1 = ExperimentFactory(team=team)
        experiment2 = ExperimentFactory(team=team)
        participant = ParticipantFactory(team=team)

        # Create 2 sessions for the same participant
        ExperimentSessionFactory(
            experiment=experiment1,
            participant=participant,
            team=team,
        )
        ExperimentSessionFactory(
            experiment=experiment2,
            participant=participant,
            team=team,
        )

        service = DashboardService(team)

        querysets = service.get_filtered_queryset_base(participant_ids=[participant.id])
        participants = querysets["participants"]

        # Count actual participants
        participant_list = list(participants)
        participant_ids_result = [p.id for p in participant_list]

        # This assertion verifies the fix is working - expecting 1, getting 2
        assert len(participant_list) == 1, (
            f"Participant duplicated! Expected 1 participant, got {len(participant_list)}. "
            f"Participant IDs: {participant_ids_result}. "
            f"This indicates missing .distinct() when filtering by participant_ids."
        )


@pytest.mark.django_db()
class TestDistinctAggregationIssues:
    """Test cases for aggregation accuracy"""

    def test_session_analytics_no_duplicate_sessions(self):
        team = TeamFactory()
        experiment = ExperimentFactory(team=team)
        participant = ParticipantFactory(team=team)
        session = ExperimentSessionFactory(
            experiment=experiment,
            participant=participant,
            team=team,
        )

        # Create 3 messages at noon to avoid midnight date boundary issues
        now = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        for i in range(3):
            ChatMessage.objects.create(
                chat=session.chat,
                message_type=ChatMessageType.HUMAN,
                content=f"Message {i}",
                created_at=now - timedelta(hours=i),
            )

        service = DashboardService(team)
        data = service.get_session_analytics_data(granularity="daily")

        # Extract session counts from the data
        session_data = data.get("sessions", [])
        total_sessions_count = sum(item["active_sessions"] for item in session_data)

        # This assertion verifies the fix is working - expecting 1, might get 3
        assert total_sessions_count == 1, (
            f"Session count inaccurate! Expected 1 session, got {total_sessions_count}. "
            f"This indicates the queryset is joining messages without proper .distinct(). "
            f"Actual data: {session_data}"
        )

    def test_bot_performance_accurate_session_count(self):
        team = TeamFactory()
        experiment = ExperimentFactory(team=team)
        participant = ParticipantFactory(team=team)
        session = ExperimentSessionFactory(
            experiment=experiment,
            participant=participant,
            team=team,
        )

        # Create multiple messages
        for i in range(5):
            ChatMessage.objects.create(
                chat=session.chat,
                message_type=ChatMessageType.HUMAN if i % 2 == 0 else ChatMessageType.AI,
                content=f"Message {i}",
            )

        service = DashboardService(team)
        data = service.get_bot_performance_summary()

        assert len(data["results"]) > 0, "Should have performance data"

        experiment_perf = data["results"][0]

        # This assertion verifies the fix is working
        assert experiment_perf["sessions"] == 1, (
            f"Session count in performance data wrong! "
            f"Expected 1 session, got {experiment_perf['sessions']}. "
            f"This indicates issues with the queryset joining behavior."
        )

    def test_bot_performance_accurate_message_count(self):
        """
        TEST: Message counts duplicated in bot performance data.
        """
        team = TeamFactory()
        experiment = ExperimentFactory(team=team)
        participant = ParticipantFactory(team=team)
        session = ExperimentSessionFactory(
            experiment=experiment,
            participant=participant,
            team=team,
        )

        # Create exactly 5 messages
        num_messages = 5
        for i in range(num_messages):
            ChatMessage.objects.create(
                chat=session.chat,
                message_type=ChatMessageType.HUMAN if i % 2 == 0 else ChatMessageType.AI,
                content=f"Message {i}",
            )

        service = DashboardService(team)
        data = service.get_bot_performance_summary()

        assert len(data["results"]) > 0, "Should have performance data"
        experiment_perf = data["results"][0]

        # This assertion verifies the fix is working
        assert experiment_perf["messages"] == num_messages, (
            f"Message count in performance data wrong! "
            f"Expected {num_messages} messages, got {experiment_perf['messages']}. "
            f"This indicates issues with counting messages through JOINs."
        )


@pytest.mark.django_db()
class TestDistinctComplexFiltering:
    """Test cases for duplicate prevention with multiple filter combinations"""

    def test_sessions_distinct_with_experiment_and_platform_filter(self):
        """
        TEST: Sessions duplicated with combined experiment and platform filters.
        """
        team = TeamFactory()
        experiment = ExperimentFactory(team=team)
        participant = ParticipantFactory(team=team)

        # Create channel
        channel = ExperimentChannelFactory(
            team=team,
            experiment=experiment,
            platform=ChannelPlatform.TELEGRAM,
        )

        # Create session with the channel
        session = ExperimentSessionFactory(
            experiment=experiment,
            participant=participant,
            team=team,
            experiment_channel=channel,
        )

        # Add messages
        for i in range(2):
            ChatMessage.objects.create(
                chat=session.chat,
                message_type=ChatMessageType.HUMAN,
                content=f"Message {i}",
            )

        service = DashboardService(team)

        querysets = service.get_filtered_queryset_base(
            experiment_ids=[experiment.id],
            platform_names=[ChannelPlatform.TELEGRAM],
        )
        sessions = querysets["sessions"]

        session_list = list(sessions)

        # This assertion verifies the fix is working
        assert len(session_list) == 1, (
            f"Session duplicated with combined filters! "
            f"Expected 1 session, got {len(session_list)}. "
            f"This indicates .distinct() isn't properly applied with multiple filters."
        )

    def test_participants_distinct_with_tag_filter(self):
        """
        TEST: Participants should not be duplicated with tag filtering.

        This test creates 3 messages all tagged with the same tag to exercise
        the tag join code path and verify .distinct() properly eliminates duplicates.
        """
        team = TeamFactory()
        experiment = ExperimentFactory(team=team)
        participant = ParticipantFactory(team=team)

        # Create a tag to trigger tag-based filtering
        tag = Tag.objects.create(
            team=team,
            name="test-tag",
            is_system_tag=False,
        )

        # Create session with messages
        session = ExperimentSessionFactory(
            experiment=experiment,
            participant=participant,
            team=team,
        )

        # Create multiple messages and tag them all with the same tag
        # This exercises the tag join path that can cause duplication
        messages = []
        for i in range(3):
            message = ChatMessage.objects.create(
                chat=session.chat,
                message_type=ChatMessageType.HUMAN,
                content=f"Message {i}",
            )
            # Add tag to each message
            message.tags.add(tag, through_defaults={"team": team})
            messages.append(message)

        service = DashboardService(team)

        # Query participants with tag filter to trigger the tag join code path
        querysets = service.get_filtered_queryset_base(tag_ids=[tag.id])
        participants = querysets["participants"]

        participant_list = list(participants)
        participant_count = len([p for p in participant_list if p.id == participant.id])

        # This assertion verifies the fix is working - expecting 1, may get 3 (one per tagged message)
        assert participant_count == 1, (
            f"Participant duplicated with tag filtering! "
            f"Expected 1 instance, got {participant_count}. "
            f"This indicates missing or ineffective .distinct() when filtering "
            f"participants by tags across chat messages (lines 101-105 in services.py)."
        )


@pytest.mark.django_db()
class TestDistinctQueryOptimization:
    """Test cases verifying duplicate prevention is applied efficiently"""

    def test_sessions_queryset_uses_distinct_after_message_join(self):
        """
        TEST: Verify sessions queryset prevents duplicates from message filtering.
        """
        team = TeamFactory()
        experiment = ExperimentFactory(team=team)
        participant = ParticipantFactory(team=team)
        session = ExperimentSessionFactory(
            experiment=experiment,
            participant=participant,
            team=team,
        )

        # Add messages
        for i in range(3):
            ChatMessage.objects.create(
                chat=session.chat,
                message_type=ChatMessageType.HUMAN,
                content=f"Message {i}",
            )

        service = DashboardService(team)

        # Get the sessions queryset and verify it uses distinct
        querysets = service.get_filtered_queryset_base()
        sessions = querysets["sessions"]

        # Check that the queryset prevents duplicates using Exists() subquery
        # The queryset should not have duplicate rows when evaluated
        session_list = list(sessions)

        # Verify no duplicates in results (functional test)
        assert len(session_list) == 1, (
            f"Sessions duplicated! Expected 1 session, got {len(session_list)}. "
            f"This indicates Exists() subquery may be missing or ineffective."
        )

    def test_experiments_queryset_uses_distinct_after_channel_join(self):
        """
        TEST: Verify experiments queryset prevents duplicates from platform filter.
        """
        team = TeamFactory()
        experiment = ExperimentFactory(team=team)

        # Create multiple channels for same experiment
        for _i in range(2):
            ExperimentChannelFactory(
                team=team,
                experiment=experiment,
                platform=ChannelPlatform.TELEGRAM,
            )

        service = DashboardService(team)

        # Get the experiments queryset with platform filter
        querysets = service.get_filtered_queryset_base(platform_names=[ChannelPlatform.TELEGRAM])
        experiments = list(querysets["experiments"])

        # Verify no duplicates in results (functional test)
        assert len(experiments) == 1, (
            f"Experiments queryset may be missing Exists() subquery after channel filter! "
            f"Found {len(experiments)} experiments (expected 1). "
            f"Without Exists() subquery, multiple channels cause duplicates."
        )


@pytest.mark.django_db()
class TestDistinctChannelPlatformFilter:
    def test_experiments_not_duplicated(self):
        team = TeamFactory()
        experiment = ExperimentFactory(team=team)

        # Create 2 channels with the same platform
        platform = ChannelPlatform.TELEGRAM
        ExperimentChannelFactory(
            team=team,
            experiment=experiment,
            platform=platform,
        )
        ExperimentChannelFactory(
            team=team,
            experiment=experiment,
            platform=platform,
        )

        service = DashboardService(team)

        # This specific filter combination triggers platform filtering (lines 91-99)
        querysets = service.get_filtered_queryset_base(platform_names=[platform])
        experiments = list(querysets["experiments"])

        assert len(experiments) == 1, f"Expected 1, got {len(experiments)}"


@pytest.mark.django_db()
class TestDistinctRegressionCases:
    """Test cases for specific regression scenarios"""

    def test_multiple_sessions_multiple_messages_complex_scenario(self):
        """
        TEST: Complex scenario with multiple sessions and messages.

        This test creates a more realistic scenario:
        - 2 participants
        - 2 experiments
        - Multiple sessions per participant-experiment combination
        - Multiple messages per session

        The bug manifests as over-counting in aggregations.
        """
        team = TeamFactory()

        participants = [ParticipantFactory(team=team) for _ in range(2)]
        experiments = [ExperimentFactory(team=team) for _ in range(2)]

        # Create 2 sessions per participant-experiment combo
        sessions = []
        for experiment in experiments:
            for participant in participants:
                for _ in range(2):
                    session = ExperimentSessionFactory(
                        experiment=experiment,
                        participant=participant,
                        team=team,
                    )
                    sessions.append(session)

                    # Add 3 messages per session
                    for msg_idx in range(3):
                        ChatMessage.objects.create(
                            chat=session.chat,
                            message_type=ChatMessageType.HUMAN if msg_idx % 2 == 0 else ChatMessageType.AI,
                            content=f"Message {msg_idx}",
                        )

        service = DashboardService(team)
        querysets = service.get_filtered_queryset_base()

        # Count results
        session_count = querysets["sessions"].count()
        participant_count = querysets["participants"].count()

        expected_sessions = len(sessions)  # 8 total (2 exp x 2 part x 2 sessions)
        expected_participants = len(participants)  # 2

        # These assertions verify the fix is working - counts should be accurate
        assert session_count == expected_sessions, (
            f"Session count wrong in complex scenario! "
            f"Expected {expected_sessions}, got {session_count}. "
            f"This indicates missing distinct() in session filtering."
        )

        assert participant_count == expected_participants, (
            f"Participant count wrong in complex scenario! "
            f"Expected {expected_participants}, got {participant_count}. "
            f"This indicates missing distinct() in participant filtering."
        )

    def test_overview_stats_with_duplicate_sessions(self):
        """
        TEST: Overview stats shows accurate total_sessions count.

        The overview stats should report accurate session counts, but
        without proper distinct(), it may inflate the numbers.
        """
        team = TeamFactory()
        experiment = ExperimentFactory(team=team)
        participant = ParticipantFactory(team=team)

        # Create 1 session with 4 messages
        session = ExperimentSessionFactory(
            experiment=experiment,
            participant=participant,
            team=team,
        )

        for i in range(4):
            ChatMessage.objects.create(
                chat=session.chat,
                message_type=ChatMessageType.HUMAN if i % 2 == 0 else ChatMessageType.AI,
                content=f"Message {i}",
            )

        service = DashboardService(team)
        stats = service.get_overview_stats()

        # This assertion verifies the fix is working
        assert stats["total_sessions"] == 1, (
            f"Overview stats reporting wrong session count! "
            f"Expected 1 session, got {stats['total_sessions']}. "
            f"This indicates missing distinct() in the overview query."
        )

        # Also verify message count is correct
        assert stats["total_messages"] == 4, (
            f"Overview stats reporting wrong message count! Expected 4 messages, got {stats['total_messages']}. "
        )

        # Verify calculated metric is based on correct counts
        expected_avg = 4.0 / 1.0  # 4 messages / 1 session
        assert stats["avg_messages_per_session"] == expected_avg, (
            f"Average messages per session wrong! "
            f"Expected {expected_avg}, got {stats['avg_messages_per_session']}. "
            f"This is a cascading issue from incorrect session count."
        )
