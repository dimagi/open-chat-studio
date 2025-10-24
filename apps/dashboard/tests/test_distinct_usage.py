"""
Failing tests for .distinct() correctness in DashboardService.

These tests are designed to FAIL with the current implementation to expose bugs
where .distinct() is missing or incorrectly applied, leading to duplicate counting
in aggregations and queryset results.

CRITICAL: DO NOT fix these tests - they intentionally fail to expose bugs in the code.
Run these tests to verify the service has .distinct() issues that need fixing.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
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
    """Test cases for session duplication issues when JOINing across messages"""

    def test_sessions_not_duplicated_with_multiple_messages_in_date_range(self):
        """
        FAILING TEST: Sessions duplicated when filtering by message date range.

        Issue: Line 62-64 in services.py
        The query filters ExperimentSession by chat__messages__created_at
        without applying .distinct(). When a session has multiple messages in
        the date range, it returns duplicate session rows (one per message).

        Expected: 1 session
        Actual with bug: 3 sessions (one per message)
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

        # This assertion WILL FAIL with the bug - expecting 1, getting 3
        assert len(session_list) == 1, (
            f"Session duplicated! Expected 1 session, got {len(session_list)}. "
            f"Session IDs: {session_ids}. "
            f"This indicates .distinct() is missing from the sessions queryset."
        )

    def test_experiments_not_duplicated_with_platform_filter(self):
        """
        FAILING TEST: Experiments duplicated when filtering by platform.

        Issue: Line 81 in services.py
        The experiments queryset filters by experimentchannel__platform
        without applying .distinct(). If an experiment has 2 channels with
        the same platform, it returns duplicate experiment rows.

        Expected: 1 experiment
        Actual with bug: 2 experiments (one per matching channel)
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

        # This assertion WILL FAIL with the bug - expecting 1, getting 2+
        assert len(experiment_list) == 1, (
            f"Experiment duplicated! Expected 1 experiment, got {len(experiment_list)}. "
            f"Experiment IDs: {experiment_ids}. "
            f"This indicates .distinct() is missing after experimentchannel filter."
        )

    def test_participants_not_duplicated_with_multiple_sessions(self):
        """
        FAILING TEST: Participants duplicated when filtering by participant_ids.

        Issue: Line 87-88 in services.py
        The participants queryset filters by experimentsession__id with
        participant_ids filter without proper .distinct(). If a participant
        has multiple sessions, it returns duplicate participant rows.

        Expected: 1 participant
        Actual with bug: 2 participants (one per session)
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

        # This assertion WILL FAIL with the bug - expecting 1, getting 2
        assert len(participant_list) == 1, (
            f"Participant duplicated! Expected 1 participant, got {len(participant_list)}. "
            f"Participant IDs: {participant_ids_result}. "
            f"This indicates missing .distinct() when filtering by participant_ids."
        )


@pytest.mark.django_db()
class TestDistinctAggregationIssues:
    """Test cases for aggregation accuracy issues due to missing distinct()"""

    def test_session_analytics_no_duplicate_sessions(self):
        """
        FAILING TEST: Session counts inaccurate due to JOINs without distinct().

        Issue: Line 156 in services.py
        The sessions.annotate() joins to chat__messages without .distinct()
        on the base queryset. When a session has multiple messages, the
        Count("id") counts duplicates, inflating the session count.

        Expected: 1 session counted
        Actual with bug: 3 sessions counted (one per message)
        """
        team = TeamFactory()
        experiment = ExperimentFactory(team=team)
        participant = ParticipantFactory(team=team)
        session = ExperimentSessionFactory(
            experiment=experiment,
            participant=participant,
            team=team,
        )

        # Create 3 messages
        now = timezone.now()
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
        total_sessions_count = sum(item["active_sessions"] for item in data.get("sessions", []))

        # This assertion WILL FAIL with the bug - expecting 1, might get 3
        assert total_sessions_count == 1, (
            f"Session count inaccurate! Expected 1 session, got {total_sessions_count}. "
            f"This indicates the queryset is joining messages without proper .distinct()."
        )

    def test_bot_performance_accurate_session_count(self):
        """
        FAILING TEST: Bot performance session counts incorrect due to JOINs.

        Issue: Line 228-236 in services.py
        The session_stats uses Count("id", distinct=True) but the base
        queryset isn't distinct'd after joining to chat__messages through
        the annotation. This can cause over-counting.

        Expected: 1 session
        Actual with bug: May count as multiple due to JOIN structure
        """
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

        # This assertion WILL FAIL with the bug
        assert experiment_perf["sessions"] == 1, (
            f"Session count in performance data wrong! "
            f"Expected 1 session, got {experiment_perf['sessions']}. "
            f"This indicates issues with the queryset joining behavior."
        )

    def test_bot_performance_accurate_message_count(self):
        """
        FAILING TEST: Message counts duplicated in bot performance data.

        Issue: Line 234 in services.py
        The Count("chat__messages", distinct=True) may still over-count if
        the base sessions queryset has duplicates from other JOINs.

        Expected: 5 messages
        Actual with bug: May count more due to JOIN duplication
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

        # This assertion WILL FAIL with the bug
        assert experiment_perf["messages"] == num_messages, (
            f"Message count in performance data wrong! "
            f"Expected {num_messages} messages, got {experiment_perf['messages']}. "
            f"This indicates issues with counting messages through JOINs."
        )


@pytest.mark.django_db()
class TestDistinctComplexFiltering:
    """Test cases for distinct() with multiple filter combinations"""

    def test_sessions_distinct_with_experiment_and_platform_filter(self):
        """
        FAILING TEST: Sessions duplicated with combined experiment and platform filters.

        When filtering by both experiment_ids and platform_names, the
        session queryset may have duplicates if not properly distinct'd.

        Expected: 1 session
        Actual with bug: May be duplicated
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

        # This assertion WILL FAIL with the bug
        assert len(session_list) == 1, (
            f"Session duplicated with combined filters! "
            f"Expected 1 session, got {len(session_list)}. "
            f"This indicates .distinct() isn't properly applied with multiple filters."
        )

    def test_participants_distinct_with_tag_filter(self):
        """
        TEST: Participants should not be duplicated with tag filtering.

        The participant filtering with tags (lines 101-105) uses OR conditions
        across multiple chat and message tag relationships that could cause
        duplicates without proper distinct().

        This test creates 3 messages all tagged with the same tag to exercise
        the tag join code path and verify .distinct() properly eliminates duplicates.

        Expected: 1 unique participant
        Actual: Should be 1 if .distinct() is properly applied (would be 3 without it)
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

        # This assertion WILL FAIL with the bug - expecting 1, may get 3 (one per tagged message)
        assert participant_count == 1, (
            f"Participant duplicated with tag filtering! "
            f"Expected 1 instance, got {participant_count}. "
            f"This indicates missing or ineffective .distinct() when filtering "
            f"participants by tags across chat messages (lines 101-105 in services.py)."
        )


@pytest.mark.django_db()
class TestDistinctQueryOptimization:
    """Test cases verifying distinct() is applied efficiently"""

    def test_sessions_queryset_uses_distinct_after_message_join(self):
        """
        FAILING TEST: Verify sessions queryset includes distinct() call.

        This test checks that when we get the base queryset with message
        filtering, it includes .distinct() to prevent duplicates.

        This tests the actual queryset structure by examining the SQL.
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

        # Check that the queryset has distinct() applied by examining the query
        # The queryset should have DISTINCT in its SQL when evaluated
        session_list = list(sessions)

        # Verify no duplicates in results (functional test)
        assert len(session_list) == 1, (
            f"Sessions duplicated! Expected 1 session, got {len(session_list)}. "
            f"This indicates .distinct() may be missing or ineffective."
        )

        # Verify the queryset query has DISTINCT
        query_str = str(sessions.query)
        assert "DISTINCT" in query_str, f"Sessions queryset missing DISTINCT in query! Query: {query_str}"

    def test_experiments_queryset_uses_distinct_after_channel_join(self):
        """
        FAILING TEST: Verify experiments queryset includes distinct() after channel join.

        This test checks that when we filter experiments by platform,
        the queryset includes .distinct() to handle multiple channels.
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
        with CaptureQueriesContext(connection) as context:
            querysets = service.get_filtered_queryset_base(platform_names=[ChannelPlatform.TELEGRAM])
            experiments = list(querysets["experiments"])

        # Find the SELECT query that fetches experiments
        exp_queries = [
            q for q in context.captured_queries if "experiment" in q["sql"].lower() and "channel" in q["sql"].lower()
        ]

        # Check if DISTINCT is present
        has_distinct = any("DISTINCT" in q["sql"] for q in exp_queries)

        # This assertion WILL FAIL with the bug
        assert has_distinct or len(experiments) == 1, (
            f"Experiments queryset may be missing DISTINCT after channel filter! "
            f"Found {len(experiments)} experiments (expected 1). "
            f"Without DISTINCT, multiple channels cause duplicates."
        )


@pytest.mark.django_db()
class TestDistinctChannelPlatformFilter:
    """Test cases specifically for line 81-82 experimentchannel filter"""

    def test_experiments_not_duplicated_line_81_82_issue(self):
        """
        FAILING TEST: Line 81-82 issue - missing distinct after experimentchannel filter.

        Code at line 81:
        experiments = experiments.filter(experimentchannel__platform__in=platform_names)

        The filter across ExperimentChannel without .distinct() creates duplicate
        rows when an experiment has multiple channels with matching platforms.

        Expected: 1 experiment
        Actual with bug: 2+ experiments (one per matching channel)
        """
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

        # This specific filter combination triggers the bug at line 81
        querysets = service.get_filtered_queryset_base(platform_names=[platform])
        experiments = list(querysets["experiments"])

        assert len(experiments) == 1, (
            f"Line 81-82 BUG: Experiment duplicated after experimentchannel filter! "
            f"Expected 1, got {len(experiments)}. "
            f"experiments = experiments.filter(experimentchannel__platform__in=platform_names) "
            f"needs .distinct() after it."
        )


@pytest.mark.django_db()
class TestDistinctRegressionCases:
    """Test cases for specific regression scenarios"""

    def test_multiple_sessions_multiple_messages_complex_scenario(self):
        """
        FAILING TEST: Complex scenario with multiple sessions and messages.

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

        expected_sessions = len(sessions)  # 8 total (2 exp × 2 part × 2 sessions)
        expected_participants = len(participants)  # 2

        # These assertions WILL FAIL with the bug - counts will be inflated
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
        FAILING TEST: Overview stats shows accurate total_sessions count.

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

        # This assertion WILL FAIL with the bug
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
