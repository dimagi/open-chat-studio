import pytest
from django.urls import reverse

from apps.trace.models import TraceStatus
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.traces import TraceFactory


def _make_trace(team, **kwargs):
    session = ExperimentSessionFactory.create(team=team)
    return TraceFactory.create(
        team=team,
        experiment=session.experiment,
        session=session,
        participant=session.participant,
        status=TraceStatus.SUCCESS,
        duration=1000,
        **kwargs,
    )


@pytest.mark.django_db()
def test_trace_table_view_filters_by_team(client, team_with_users):
    """The trace list view must return only traces belonging to the requesting team."""
    team = team_with_users
    user = team.members.first()
    other_team = TeamFactory.create()

    own_trace = _make_trace(team)
    foreign_trace = _make_trace(other_team)

    client.force_login(user)
    response = client.get(reverse("trace:table", args=[team.slug]))

    assert response.status_code == 200
    visible_ids = {row.record.id for row in response.context_data["table"].rows}
    assert own_trace.id in visible_ids
    assert foreign_trace.id not in visible_ids
