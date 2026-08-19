from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from time_machine import travel

from apps.chatbots.tables import ChatbotSessionsTable, ChatbotTable
from apps.experiments.models import Experiment, ExperimentSession
from apps.generics.actions import Action, chip_action
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory


@pytest.mark.django_db()
def test_chatbot_table_redirect_url(team_with_users):
    team = team_with_users
    user = team.members.first()
    experiment = Experiment.objects.create(
        name="Redirect Test", description="Testing redirect URLs", owner=user, team=team, is_archived=False
    )

    # Clear cached team slugs to avoid stale entries from previous tests
    cache.delete(f"team_slug:{team.id}")

    table = ChatbotTable(Experiment.objects.filter(id=experiment.id))
    row_attrs = list(table.rows)[0].attrs

    expected_url = reverse("chatbots:single_chatbot_home", args=[team.slug, experiment.id])
    assert row_attrs["data-redirect-url"] == expected_url


@pytest.mark.django_db()
class TestSessionsTableLastActivityColumn:
    """The "Last activity" column falls back to ``created_at`` for sessions whose participant never
    sent a message — those have a null ``last_activity_at`` (e.g. sessions created by
    ``trigger_bot_api``) and used to render an empty cell.
    """

    @pytest.fixture()
    def sessions(self, team_with_users):
        """``never_messaged`` was created most recently; ``messaged`` has older participant activity."""
        experiment = ExperimentFactory.create(team=team_with_users)
        with travel("2025-01-10 10:00:00", tick=False):
            messaged = ExperimentSessionFactory.create(team=team_with_users, experiment=experiment)
        messaged.last_activity_at = timezone.datetime.fromisoformat("2025-03-10T10:00:00+00:00")
        messaged.save()
        with travel("2025-05-10 10:00:00", tick=False):
            never_messaged = ExperimentSessionFactory.create(team=team_with_users, experiment=experiment)
        return never_messaged, messaged

    def _cell(self, session):
        table = ChatbotSessionsTable(ExperimentSession.objects.filter(id=session.id))
        return str(list(table.rows)[0].get_cell("last_activity"))

    def test_falls_back_to_created_at(self, sessions):
        never_messaged, _ = sessions
        assert never_messaged.last_activity_at is None
        assert never_messaged.created_at.isoformat() in self._cell(never_messaged)

    def test_uses_last_activity_at_when_set(self, sessions):
        _, messaged = sessions
        cell = self._cell(messaged)
        assert messaged.last_activity_at.isoformat() in cell
        assert messaged.created_at.isoformat() not in cell

    def test_default_ordering_uses_the_same_expression(self, sessions, team_with_users):
        """The most recently created session sorts first even with no participant activity —
        ordering on the raw column would drop it to the bottom while it displays a recent time.
        """
        never_messaged, messaged = sessions
        queryset = ExperimentSession.objects.get_table_queryset(team_with_users)
        assert list(queryset) == [never_messaged, messaged]

    def test_column_sort_uses_the_same_expression(self, sessions, team_with_users):
        never_messaged, messaged = sessions
        queryset = ExperimentSession.objects.get_table_queryset(team_with_users)
        table = ChatbotSessionsTable(queryset, order_by="last_activity")
        assert [row.record for row in table.rows] == [messaged, never_messaged]

        table = ChatbotSessionsTable(queryset, order_by="-last_activity")
        assert [row.record for row in table.rows] == [never_messaged, messaged]


def test_chatbot_chip_action():
    team = Mock(slug="test-team")
    experiment = Mock(
        spec=Experiment,
        name="Test Experiment",
        description="Test description",
        team=team,
        public_id=str(uuid4()),
    )
    session = Mock(
        spec=ExperimentSession,
        experiment=experiment,
        external_id=str(uuid4()),
        created_at="2025-03-01T10:00:00Z",
        team=team,
    )

    def custom_url_factory(*args):
        return reverse(
            "chatbots:chatbot_session_view",
            args=[team.slug, experiment.public_id, session.external_id],
        )

    action = chip_action(label="Session Details", url_factory=custom_url_factory)

    assert isinstance(action, Action)
    assert action.label == "Session Details"
    assert callable(action.url_factory)
    url = action.url_factory(None, None, session, None)
    expected_url = reverse(
        "chatbots:chatbot_session_view",
        args=[team.slug, experiment.public_id, session.external_id],
    )
    assert url == expected_url


@pytest.mark.django_db()
class TestSessionsTableParticipantColumn:
    """Long participant labels used to wrap, stretching the chip to fill the cell and giving the
    column ragged row heights."""

    def _cell(self, session):
        table = ChatbotSessionsTable(ExperimentSession.objects.filter(id=session.id))
        return str(list(table.rows)[0].get_cell("participant"))

    def test_chip_stays_on_one_line(self, team_with_users):
        participant = ParticipantFactory.create(
            team=team_with_users, name="", identifier="a-very-long-participant@dimagi-associate.com"
        )
        session = ExperimentSessionFactory.create(team=team_with_users, participant=participant)
        cell = self._cell(session)
        assert "truncate" in cell
        assert f'title="{participant.identifier}"' in cell
