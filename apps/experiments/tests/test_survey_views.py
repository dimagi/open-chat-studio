from django.test import RequestFactory
from django.urls import reverse

from apps.experiments.models import Survey
from apps.experiments.views.survey import SurveyTableView


class TestSurveyTableView:
    def test_get_queryset(self, experiment):
        assert experiment.pre_survey is not None
        experiment.create_new_version()
        assert Survey.objects.count() == 2

        request = RequestFactory().get(reverse("experiments:survey_table", args=[experiment.team.slug]))
        request.team = experiment.team
        view = SurveyTableView()
        view.request = request
        assert list(view.get_queryset().all()) == [experiment.pre_survey]
