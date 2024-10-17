import pytest
from django.test import RequestFactory
from django.urls import reverse

from apps.experiments.models import ConsentForm
from apps.experiments.views.consent import ConsentFormTableView
from apps.utils.factories.experiment import ConsentFormFactory, ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


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


@pytest.mark.django_db()
def test_delete(client):
    team = TeamWithUsersFactory()
    user = team.members.first()
    form = ConsentFormFactory(team=team, is_default=False)
    experiment = ExperimentFactory(consent_form=form)
    client.force_login(user)
    url = reverse("experiments:consent_delete", args=[team.slug, form.id])
    response = client.delete(url)
    assert response.status_code == 200
    form.refresh_from_db()
    assert form.is_archived is True

    experiment.refresh_from_db()
    assert experiment.consent_form == ConsentForm.objects.get(team=team, is_default=True)
