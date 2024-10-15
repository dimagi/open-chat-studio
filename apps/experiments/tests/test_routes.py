import pytest
from django.urls import reverse

from apps.experiments.models import ExperimentRoute
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
def test_delete(client):
    team = TeamWithUsersFactory()
    user = team.members.first()
    client.force_login(user)
    experiment = ExperimentFactory(team=team)
    route = ExperimentRoute.objects.create(
        team=team, parent=experiment, child=ExperimentFactory(team=team), keyword="keyword1"
    )
    url = reverse("experiments:experiment_route_delete", args=[experiment.team.slug, experiment.id, route.id])
    response = client.delete(url)
    assert response.status_code == 200
    route.refresh_from_db()
    assert route.is_archived is True
