import pytest
from django.urls import reverse

from apps.trace.models import TraceStatus
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory
from apps.utils.factories.traces import TraceFactory


def _detail_url(team, trace):
    return reverse("trace:trace_detail", args=[team.slug, trace.pk])


@pytest.fixture()
def experiment(team):
    return ExperimentFactory.create(team=team)


@pytest.fixture()
def session(team, experiment):
    return ExperimentSessionFactory.create(team=team, experiment=experiment)


@pytest.fixture()
def participant(team):
    return ParticipantFactory.create(team=team)


@pytest.mark.django_db()
class TestTraceDetailView:
    def test_renders_successfully(self, anon_client, team, user, experiment, session, participant):
        trace = TraceFactory.create(
            team=team,
            experiment=experiment,
            session=session,
            participant=participant,
            status=TraceStatus.SUCCESS,
            duration=3090,
        )
        anon_client.force_login(user)
        response = anon_client.get(_detail_url(team, trace))
        assert response.status_code == 200
