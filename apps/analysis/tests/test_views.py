import pytest
from django.urls import reverse

from apps.analysis.models import TranscriptAnalysis
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.mark.django_db()
def test_analysis_detail_includes_chat_widget_launcher(client, team_with_users):
    """The sessions table swapped into this page renders Continue Chat buttons, which call
    ocsContinueSessionChat. Any page rendering ChatbotSessionsTable must include the launcher."""
    user = team_with_users.members.first()
    experiment = ExperimentFactory.create(team=team_with_users)
    session = ExperimentSessionFactory.create(team=team_with_users, experiment=experiment)
    analysis = TranscriptAnalysis.objects.create(
        team=team_with_users, experiment=experiment, name="Analysis", created_by=user
    )
    analysis.sessions.add(session)
    client.force_login(user)

    url = reverse("analysis:detail", args=[team_with_users.slug, analysis.id])
    response = client.get(url)

    assert response.status_code == 200
    assert "ocsContinueSessionChat = function" in response.content.decode()
