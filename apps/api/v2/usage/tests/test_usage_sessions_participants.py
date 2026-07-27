import pytest
import time_machine
from dateutil.relativedelta import relativedelta
from django.urls import reverse
from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.utils.factories.experiment import ExperimentSessionFactory, ParticipantFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

USAGE_URL = "api:v2:usage"


def _add_messages(session, *, human=0, ai=0, when=None):
    kwargs = {"created_at": when} if when else {}
    for message_type, count in ((ChatMessageType.HUMAN, human), (ChatMessageType.AI, ai)):
        for _ in range(count):
            ChatMessage.objects.create(chat=session.chat, message_type=message_type, content="x", **kwargs)


@pytest.mark.django_db()
def test_sessions_metric_counts_current_month_excluding_eval_and_setup():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    ExperimentSessionFactory.create_batch(3, team=team, status=SessionStatus.ACTIVE)
    ExperimentSessionFactory.create(team=team, status=SessionStatus.SETUP)  # never engaged, excluded
    ExperimentSessionFactory.create(
        team=team, status=SessionStatus.ACTIVE, platform=ChannelPlatform.EVALUATIONS
    )  # eval-harness session, excluded

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": "sessions"})

    assert response.status_code == 200
    assert response.json()["results"]["sessions"] == 3


@pytest.mark.django_db()
def test_participants_metric_counts_distinct_active():
    """Two sessions share one participant, a third has its own; all have messages: 3 sessions, 2
    distinct active participants."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    shared = ParticipantFactory.create(team=team)
    _add_messages(ExperimentSessionFactory.create(team=team, participant=shared, status=SessionStatus.ACTIVE), human=1)
    _add_messages(ExperimentSessionFactory.create(team=team, participant=shared, status=SessionStatus.ACTIVE), human=1)
    _add_messages(ExperimentSessionFactory.create(team=team, status=SessionStatus.ACTIVE), human=1)

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": ["sessions", "participants"]})

    assert response.status_code == 200
    results = response.json()["results"]
    assert results["sessions"] == 3
    assert results["participants"] == 2


@pytest.mark.django_db()
def test_sessions_and_participants_use_different_windows():
    """``sessions`` windows on session creation, ``participants`` on message activity, so the two can
    diverge: a participant active this month in a session started last month counts for
    ``participants`` but not ``sessions``, and vice versa."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    last_month = timezone.now() - relativedelta(months=1)

    # Session started last month, but its participant sends a message this month → active now.
    old_session = ExperimentSessionFactory.create(team=team, status=SessionStatus.ACTIVE)
    ExperimentSession.objects.filter(pk=old_session.pk).update(created_at=last_month)
    _add_messages(old_session, human=1)  # message defaults to now (this month)

    # Session started this month, but its only activity was last month → not active now.
    new_session = ExperimentSessionFactory.create(team=team, status=SessionStatus.ACTIVE)
    _add_messages(new_session, human=1, when=last_month)

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": ["sessions", "participants"]})

    assert response.status_code == 200
    results = response.json()["results"]
    assert results["sessions"] == 1  # only new_session was created in the window
    assert results["participants"] == 1  # only old_session's participant was active in the window


@pytest.mark.django_db()
def test_participants_excludes_system_only_activity():
    """A participant whose only message is an internal ``system`` message is not "active": the
    ``participants`` metric counts the same human/AI categories the ``messages`` metric surfaces."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    session = ExperimentSessionFactory.create(team=team)
    ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.SYSTEM, content="x")

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": ["messages", "participants"]})

    assert response.status_code == 200
    results = response.json()["results"]
    assert results["messages"] == {"human": 0, "ai": 0, "total": 0}
    assert results["participants"] == 0


@pytest.mark.django_db()
def test_multi_metric_response_has_one_block_each():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    session = ExperimentSessionFactory.create(team=team, status=SessionStatus.ACTIVE)
    _add_messages(session, human=2, ai=1)

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": ["messages", "sessions", "participants"]},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results["messages"] == {"human": 2, "ai": 1, "total": 3}
    assert results["sessions"] == 1
    assert results["participants"] == 1


@pytest.mark.django_db()
def test_participant_filter_applies_to_new_metrics():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    target = ExperimentSessionFactory.create(team=team, status=SessionStatus.ACTIVE)
    _add_messages(target, human=1)
    _add_messages(
        ExperimentSessionFactory.create(team=team, participant=target.participant, status=SessionStatus.ACTIVE),
        human=1,
    )
    # different participant, excluded by the filter
    _add_messages(ExperimentSessionFactory.create(team=team, status=SessionStatus.ACTIVE), human=1)

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": ["sessions", "participants"], "participant": str(target.participant.public_id)},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results["sessions"] == 2  # both of the target participant's sessions
    assert results["participants"] == 1  # a participant filter narrows to a single participant


@pytest.mark.django_db()
def test_team_isolation():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    other_team = TeamWithUsersFactory.create()
    ExperimentSessionFactory.create_batch(4, team=other_team, status=SessionStatus.ACTIVE)

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": ["sessions", "participants"]})

    assert response.status_code == 200
    results = response.json()["results"]
    assert results["sessions"] == 0
    assert results["participants"] == 0


@pytest.mark.django_db()
@time_machine.travel("2026-03-15T12:00:00+00:00")
def test_period_filter_applies_to_sessions():
    """``created_at`` is ``auto_now_add`` on ExperimentSession, so travel to each month to place the
    sessions, then query. The current window must not include last month's sessions."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    with time_machine.travel("2026-02-10T12:00:00+00:00"):
        ExperimentSessionFactory.create_batch(2, team=team, status=SessionStatus.ACTIVE)  # previous month
    ExperimentSessionFactory.create(team=team, status=SessionStatus.ACTIVE)  # current month (March)

    client = ApiTestClient(user, team)
    current = client.get(reverse(USAGE_URL), {"metric": "sessions", "period": "current_month"}).json()
    previous = client.get(reverse(USAGE_URL), {"metric": "sessions", "period": "previous_month"}).json()

    assert current["results"]["sessions"] == 1
    assert previous["results"]["sessions"] == 2
