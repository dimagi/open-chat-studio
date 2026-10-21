import pytest
from django.urls import reverse
from waffle.testutils import override_flag

from apps.analysis.models import TranscriptAnalysis
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.human_annotations import AnnotationItemFactory, AnnotationQueueFactory


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


@pytest.mark.django_db()
def test_analysis_detail_does_not_show_the_annotation_queues_column(client, team_with_users):
    """The analysis page reuses the sessions table but is not where queue membership is reviewed."""
    user = team_with_users.members.first()
    experiment = ExperimentFactory.create(team=team_with_users)
    session = ExperimentSessionFactory.create(team=team_with_users, experiment=experiment)
    AnnotationItemFactory.create(
        queue=AnnotationQueueFactory.create(team=team_with_users, name="Safety QA"),
        team=team_with_users,
        session=session,
    )
    analysis = TranscriptAnalysis.objects.create(
        team=team_with_users, experiment=experiment, name="Analysis", created_by=user
    )
    analysis.sessions.add(session)
    client.force_login(user)

    with override_flag("flag_human_annotations", active=True):
        url = reverse("analysis:detail", args=[team_with_users.slug, analysis.id])
        html = client.get(url, HTTP_HX_REQUEST="true").content.decode()

    assert "Annotation Queues" not in html
    assert "Safety QA" not in html
