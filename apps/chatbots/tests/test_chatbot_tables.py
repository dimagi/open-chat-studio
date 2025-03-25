import pytest
from django.urls import reverse

from apps.chatbots.tables import ChatbotTable, chatbot_chip_action
from apps.experiments.models import Experiment, ExperimentSession
from apps.generics.actions import Action


@pytest.mark.django_db()
def test_chatbot_table_render(team_with_users):
    team = team_with_users
    user = team.members.first()
    experiment = Experiment.objects.create(
        name="Test Experiment", description="This is a test description.", owner=user, team=team, is_archived=False
    )
    table = ChatbotTable(Experiment.objects.filter(id=experiment.id))
    row = list(table.rows)[0]

    assert row.get_cell("name") == "Test Experiment"
    assert row.get_cell("description") == "This is a test description."
    assert row.get_cell("owner") == user.username


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


@pytest.mark.django_db()
def test_chatbot_chip_action(team_with_users):
    team = team_with_users
    user = team.members.first()
    experiment = Experiment.objects.create(
        name="Test Experiment",
        description="Test description",
        owner=user,
        team=team,
    )
    session = ExperimentSession.objects.create(
        experiment=experiment,
        external_id="session1",
        created_at="2025-03-01T10:00:00Z",
        team=team,
    )

    action = chatbot_chip_action(label="Session Details")

    assert isinstance(action, Action)
    assert action.label == "Session Details"
    assert callable(action.url_factory)
    url = action.url_factory(None, None, session, None)
    expected_url = reverse(
        "chatbots:chatbot_session_view",
        args=[team.slug, experiment.public_id, session.external_id],
    )
    assert url == expected_url
