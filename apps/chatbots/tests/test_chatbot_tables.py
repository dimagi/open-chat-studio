from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.urls import reverse

from apps.chatbots.tables import ChatbotTable
from apps.experiments.models import Experiment, ExperimentSession
from apps.generics.actions import Action, chip_action


@pytest.mark.django_db()
def test_chatbot_table_redirect_url(team_with_users):
    team = team_with_users
    user = team.members.first()
    experiment = Experiment.objects.create(
        name="Redirect Test", description="Testing redirect URLs", owner=user, team=team, is_archived=False
    )

    table = ChatbotTable(Experiment.objects.filter(id=experiment.id))
    row_attrs = list(table.rows)[0].attrs

    expected_url = reverse("chatbots:single_chatbot_home", args=[team.slug, experiment.id])
    assert row_attrs["data-redirect-url"] == expected_url


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
