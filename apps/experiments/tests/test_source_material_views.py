from django.test import RequestFactory
from django.urls import reverse

from apps.experiments.models import SourceMaterial
from apps.experiments.views.source_material import SourceMaterialTableView


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
