import pytest
from django.urls import reverse

from apps.chatbots.tables import ChatbotTable
from apps.experiments.models import Experiment


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
