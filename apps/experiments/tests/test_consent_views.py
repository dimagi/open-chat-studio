from django.test import RequestFactory
from django.urls import reverse

from apps.experiments.views.consent import ConsentFormTableView


class TestConsentFormTableView:
    def test_get_queryset(self, experiment):
        assert experiment.consent_form is not None
        experiment.create_new_version()

        request = RequestFactory().get(reverse("experiments:consent_table", args=[experiment.team.slug]))
        request.team = experiment.team
        view = ConsentFormTableView()
        view.request = request
        for consent_form in view.get_queryset().all():
            assert consent_form.is_working_version is True
