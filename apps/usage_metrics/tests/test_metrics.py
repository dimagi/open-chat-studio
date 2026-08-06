"""Unit tests for the usage_metrics filter vocabulary and metric functions."""

import dataclasses
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
import time_machine

from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import SessionStatus
from apps.usage_metrics import metrics
from apps.usage_metrics.filters import UsageFilters
from apps.utils.factories.annotations import CustomTaggedItemFactory, TagFactory
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory


class TestUsageFilters:
    def test_defaults_mean_unfiltered(self):
        filters = UsageFilters()
        assert filters.experiment_ids is None
        assert filters.participant_ids is None
        assert filters.platform is None
        assert filters.tag_ids is None
        assert filters.include_archived is True

    def test_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            UsageFilters().platform = "web"  # type: ignore


_TZ = ZoneInfo("UTC")
_START = datetime(2026, 6, 1, tzinfo=UTC)
_END = datetime(2026, 6, 15, tzinfo=UTC)
_MID = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)


def _message(session, *, message_type=ChatMessageType.HUMAN, when=_MID):
    return ChatMessage.objects.create(chat=session.chat, message_type=message_type, content="x", created_at=when)


@pytest.fixture()
def frozen_time():
    with time_machine.travel(_MID, tick=False):
        yield


@pytest.mark.django_db()
@pytest.mark.usefixtures("frozen_time")
class TestMessages:
    """Reproduces the v2 usage API's current message read: half-open window,
    every session type (evaluations included), total = human + ai."""

    def test_counts_human_ai_and_total(self):
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        _message(session, message_type=ChatMessageType.HUMAN)
        _message(session, message_type=ChatMessageType.AI)
        _message(session, message_type=ChatMessageType.SYSTEM)

        counts = metrics.messages(team, start=_START, end=_END, filters=UsageFilters())

        assert counts == {"human": 1, "ai": 1, "total": 2}

    def test_window_is_half_open(self):
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        _message(session, when=_START)
        _message(session, when=_END)

        counts = metrics.messages(team, start=_START, end=_END, filters=UsageFilters())

        assert counts["total"] == 1

    def test_includes_evaluation_sessions(self):
        team = TeamFactory.create()
        eval_session = ExperimentSessionFactory.create(
            team=team,
            experiment_channel=ExperimentChannelFactory(team=team, platform=ChannelPlatform.EVALUATIONS),
        )
        _message(eval_session)

        counts = metrics.messages(team, start=_START, end=_END, filters=UsageFilters())

        assert counts["total"] == 1

    def test_empty_id_list_means_matched_nobody(self):
        team = TeamFactory.create()
        _message(ExperimentSessionFactory.create(team=team))

        counts = metrics.messages(team, start=_START, end=_END, filters=UsageFilters(participant_ids=[]))

        assert counts["total"] == 0

    def test_filters_by_experiment_and_participant(self):
        team = TeamFactory.create()
        keep = ExperimentSessionFactory.create(team=team)
        _message(keep)
        _message(ExperimentSessionFactory.create(team=team))

        by_experiment = metrics.messages(
            team, start=_START, end=_END, filters=UsageFilters(experiment_ids=[keep.experiment_id])
        )
        by_participant = metrics.messages(
            team, start=_START, end=_END, filters=UsageFilters(participant_ids=[keep.participant_id])
        )

        assert by_experiment["total"] == 1
        assert by_participant["total"] == 1

    def test_filters_by_platform(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        web = ExperimentSessionFactory.create(team=team, experiment=experiment, platform="web")
        api = ExperimentSessionFactory.create(team=team, experiment=experiment, platform="api")
        _message(web)
        _message(api)

        counts = metrics.messages(team, start=_START, end=_END, filters=UsageFilters(platform="web"))

        assert counts["total"] == 1

    def test_tag_filter_narrows_to_tagged_conversations(self):
        team = TeamFactory.create()
        tagged = ExperimentSessionFactory.create(team=team)
        untagged = ExperimentSessionFactory.create(team=team)
        _message(tagged)
        _message(untagged)
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=team, tag=tag, target=tagged.chat)

        counts = metrics.messages(team, start=_START, end=_END, filters=UsageFilters(tag_ids=[tag.id]))

        assert counts["total"] == 1

    def test_tag_filter_ignores_cross_team_tag_links(self):
        """Same inconsistent-link regression as the dashboard builder: a link
        row with a foreign team_id must not qualify a local chat."""
        team = TeamFactory.create()
        foreign_team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        _message(session)
        tag = TagFactory.create(team=team)
        CustomTaggedItemFactory.create(team=foreign_team, tag=tag, target=session.chat)

        counts = metrics.messages(team, start=_START, end=_END, filters=UsageFilters(tag_ids=[tag.id]))

        assert counts["total"] == 0


@pytest.mark.django_db()
@pytest.mark.usefixtures("frozen_time")
class TestSessionCounts:
    def test_sessions_started_excludes_setup_and_evaluations(self):
        team = TeamFactory.create()
        ExperimentSessionFactory.create(team=team, status=SessionStatus.ACTIVE)
        ExperimentSessionFactory.create(team=team, status=SessionStatus.SETUP)
        ExperimentSessionFactory.create(
            team=team,
            experiment_channel=ExperimentChannelFactory(team=team, platform=ChannelPlatform.EVALUATIONS),
        )

        assert metrics.sessions_started(team, start=_START, end=_END, filters=UsageFilters()) == 1

    def test_sessions_in_setup_complements_sessions_started(self):
        """sessions_started + sessions_in_setup = non-evaluation sessions
        created in the window."""
        team = TeamFactory.create()
        ExperimentSessionFactory.create_batch(2, team=team, status=SessionStatus.ACTIVE)
        ExperimentSessionFactory.create(team=team, status=SessionStatus.SETUP)

        started = metrics.sessions_started(team, start=_START, end=_END, filters=UsageFilters())
        in_setup = metrics.sessions_in_setup(team, start=_START, end=_END, filters=UsageFilters())

        assert (started, in_setup) == (2, 1)

    def test_sessions_active_counts_any_message_in_closed_window(self):
        """The dashboard's current definition, unchanged: any message type
        qualifies, SETUP sessions count, the window end is inclusive."""
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        system_only = ExperimentSessionFactory.create(team=team, experiment=experiment, status=SessionStatus.SETUP)
        _message(system_only, message_type=ChatMessageType.SYSTEM)
        boundary = ExperimentSessionFactory.create(team=team, experiment=experiment)
        _message(boundary, when=_END)
        ExperimentSessionFactory.create(team=team, experiment=experiment)  # silent

        assert metrics.sessions_active(team, start=_START, end=_END, filters=UsageFilters()) == 2


@pytest.mark.django_db()
@pytest.mark.usefixtures("frozen_time")
class TestActiveParticipants:
    def test_counts_distinct_human_or_ai_authors(self):
        team = TeamFactory.create()
        human = ExperimentSessionFactory.create(team=team)
        _message(human, message_type=ChatMessageType.HUMAN)
        _message(human, message_type=ChatMessageType.AI)
        system_only = ExperimentSessionFactory.create(team=team)
        _message(system_only, message_type=ChatMessageType.SYSTEM)

        assert metrics.active_participants(team, start=_START, end=_END, filters=UsageFilters()) == 1


@pytest.mark.django_db()
@pytest.mark.usefixtures("frozen_time")
class TestTimeseries:
    def test_messages_timeseries_keys_by_local_bucket_date(self):
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        _message(session, when=datetime(2026, 6, 9, 23, 0, tzinfo=UTC))
        _message(session, when=_MID)

        series = metrics.messages_timeseries(
            team, start=_START, end=_END, granularity="daily", tz=_TZ, filters=UsageFilters()
        )

        assert series == {
            date(2026, 6, 9): {"human": 1, "ai": 0, "total": 1},
            date(2026, 6, 10): {"human": 1, "ai": 0, "total": 1},
        }

    def test_messages_timeseries_buckets_in_request_timezone(self):
        """23:00 UTC on the 9th is already the 10th in UTC+2, so the bucket
        date shifts with the tz - matching the API's TruncDate(tzinfo=tz)."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        _message(session, when=datetime(2026, 6, 9, 23, 0, tzinfo=UTC))

        series = metrics.messages_timeseries(
            team,
            start=_START,
            end=_END,
            granularity="daily",
            tz=ZoneInfo("Africa/Johannesburg"),
            filters=UsageFilters(),
        )

        assert list(series) == [date(2026, 6, 10)]

    def test_sessions_started_timeseries_buckets_on_creation(self):
        team = TeamFactory.create()
        ExperimentSessionFactory.create(team=team, status=SessionStatus.ACTIVE)

        series = metrics.sessions_started_timeseries(
            team, start=_START, end=_END, granularity="daily", tz=_TZ, filters=UsageFilters()
        )

        assert series == {date(2026, 6, 10): 1}

    def test_active_participants_timeseries_counts_distinct_per_bucket(self):
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        _message(session, message_type=ChatMessageType.HUMAN)
        _message(session, message_type=ChatMessageType.AI)

        series = metrics.active_participants_timeseries(
            team, start=_START, end=_END, granularity="daily", tz=_TZ, filters=UsageFilters()
        )

        assert series == {date(2026, 6, 10): 1}

    def test_sessions_in_setup_timeseries(self):
        team = TeamFactory.create()
        ExperimentSessionFactory.create(team=team, status=SessionStatus.SETUP)

        series = metrics.sessions_in_setup_timeseries(
            team, start=_START, end=_END, granularity="daily", tz=_TZ, filters=UsageFilters()
        )

        assert series == {date(2026, 6, 10): 1}
