import pytest
from django.test import RequestFactory
from django.urls import reverse

from apps.experiments.views.survey import SurveyTableView
from apps.utils.factories.experiment import ExperimentFactory, SurveyFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
class TestSurveyTableView:
    def test_get_queryset(self, experiment):
        # NOTE: minimally adjusted in Task 3 to keep collection working; rewritten in Task 4.
        survey = SurveyFactory.create(team=experiment.team)

        request = RequestFactory().get(reverse("experiments:survey_table", args=[experiment.team.slug]))
        request.team = experiment.team
        view = SurveyTableView()
        view.request = request
        assert list(view.get_queryset().all()) == [survey]


@pytest.mark.django_db()
def test_delete(client):
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    survey = SurveyFactory.create(team=team)
    experiment = ExperimentFactory.create(team=team)
    client.force_login(user)
    url = reverse("experiments:survey_delete", args=[experiment.team.slug, survey.id])
    response = client.delete(url)
    assert response.status_code == 200
    survey.refresh_from_db()
    assert survey.is_archived is True
