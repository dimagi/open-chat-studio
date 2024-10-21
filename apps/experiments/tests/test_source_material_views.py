import pytest
from django.test import RequestFactory
from django.urls import reverse

from apps.experiments.models import SourceMaterial
from apps.experiments.views.source_material import SourceMaterialTableView
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


class TestSourceMaterialTableView:
    def test_get_queryset(self, experiment):
        experiment.source_material = SourceMaterial.objects.create(
            team=experiment.team, owner=experiment.owner, topic="Testing", description="descripto", material="Meh"
        )
        experiment.save()
        experiment.create_new_version()
        assert SourceMaterial.objects.count() == 2

        request = RequestFactory().get(reverse("experiments:source_material_table", args=[experiment.team.slug]))
        request.team = experiment.team
        view = SourceMaterialTableView()
        view.request = request
        assert list(view.get_queryset().all()) == [experiment.source_material]


@pytest.mark.django_db()
def test_delete(client):
    team = TeamWithUsersFactory()
    user = team.members.first()
    experiment = ExperimentFactory(team=team)
    client.force_login(user)
    source_material = SourceMaterial.objects.create(
        team=team, owner=user, topic="Testing", description="descripto", material="Meh"
    )
    url = reverse("experiments:source_material_delete", args=[experiment.team.slug, source_material.id])
    response = client.delete(url)
    assert response.status_code == 200
    source_material.refresh_from_db()
    assert source_material.is_archived is True

    experiment.refresh_from_db()
    assert experiment.source_material is None
