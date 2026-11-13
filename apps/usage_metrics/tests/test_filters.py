"""The predicate vocabulary: what activity metrics leave out, defined once (ADR-0051)."""

from pathlib import Path

import pytest

from apps.channels.models import ChannelPlatform
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.usage_metrics.filters import activity_messages, activity_sessions, evaluation_platform
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory

REPO_ROOT = Path(__file__).resolve().parents[3]

EVALUATIONS_LITERAL = "ChannelPlatform.EVALUATIONS"
SETUP_LITERAL = "SessionStatus.SETUP"

# The metric surfaces and the literals none of them may spell out again. The chatbot views module
# also gates a session view on `SessionStatus.SETUP`, which is not a metric read, so only the
# evaluation literal is forbidden there.
METRIC_SURFACES = [
    pytest.param("apps/usage_metrics/metrics.py", (EVALUATIONS_LITERAL, SETUP_LITERAL), id="usage_metrics.metrics"),
    pytest.param(
        "apps/usage_metrics/dashboard_querysets.py",
        (EVALUATIONS_LITERAL, SETUP_LITERAL),
        id="usage_metrics.dashboard_querysets",
    ),
    pytest.param("apps/dashboard/services.py", (EVALUATIONS_LITERAL, SETUP_LITERAL), id="dashboard.services"),
    pytest.param(
        "apps/dashboard/engagement_service.py", (EVALUATIONS_LITERAL, SETUP_LITERAL), id="dashboard.engagement"
    ),
    pytest.param("apps/api/v2/usage/services.py", (EVALUATIONS_LITERAL, SETUP_LITERAL), id="api.usage"),
    pytest.param(
        "apps/cost_tracking/services/reporting.py", (EVALUATIONS_LITERAL, SETUP_LITERAL), id="cost_tracking.reporting"
    ),
]


@pytest.mark.parametrize(("module", "literals"), METRIC_SURFACES)
def test_no_surface_spells_the_exclusions_out_again(module, literals):
    source = (REPO_ROOT / module).read_text()

    assert [literal for literal in literals if literal in source] == []


def test_a_negated_predicate_keeps_null_rows():
    """Django guards a negated lookup on a nullable column with `IS NOT NULL`, so the row with no
    platform is not swept out with the evaluation ones."""
    sql = str(ExperimentSession.objects.exclude(evaluation_platform()).query)

    assert '"platform" IS NOT NULL' in sql


def _session(team, shape):
    if shape == "null-platform":
        return ExperimentSessionFactory.create(team=team, platform=None)
    if shape == "evaluations":
        return ExperimentSessionFactory.create(team=team, platform=ChannelPlatform.EVALUATIONS)
    if shape == "setup":
        return ExperimentSessionFactory.create(team=team, status=SessionStatus.SETUP)
    return ExperimentSessionFactory.create(team=team)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("shape", "counted"),
    [
        pytest.param("active", True, id="active-session"),
        pytest.param("null-platform", True, id="null-platform-is-not-evaluations"),
        pytest.param("evaluations", False, id="evaluation-session"),
        pytest.param("setup", False, id="setup-session"),
    ],
)
def test_activity_sessions_and_activity_messages_count_the_same_sessions(shape, counted):
    """One session per shape, carrying a turn and a SYSTEM message: the session counts or not, and
    its turn counts exactly when the session does (ADR-0051)."""
    team = TeamFactory.create()
    session = _session(team, shape)
    ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="x")
    ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.SYSTEM, content="x")

    sessions = activity_sessions(ExperimentSession.objects.filter(team=team))
    messages = activity_messages(ChatMessage.objects.filter(chat__team=team))

    assert (sessions.count(), messages.count()) == ((1, 1) if counted else (0, 0))


@pytest.mark.django_db()
def test_a_message_whose_chat_has_no_session_stays_in_the_universe():
    team = TeamFactory.create()
    chat = Chat.objects.create(team=team, name="no session")
    ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="x")

    assert activity_messages(ChatMessage.objects.filter(chat__team=team)).count() == 1
