import re
from html import unescape
from urllib.parse import parse_qs, urlparse

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
def test_trace_detail_view_renders_filter_links(client, team_with_users):
    """The trace detail page links to the trace table pre-filtered by session/chatbot/participant."""
    team = team_with_users
    user = team.members.first()
    trace = _make_trace(team)

    client.force_login(user)
    response = client.get(reverse("trace:trace_detail", args=[team.slug, trace.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    home_url = reverse("trace:home", args=[team.slug])

    # Collect the filter query params from each link pointing at the trace table home.
    links = {}
    for href in re.findall(rf'href="({re.escape(home_url)}\?[^"]+)"', content):
        params = parse_qs(urlparse(unescape(href)).query)
        links[
            params["f_session_id"][0]
            if "f_session_id" in params
            else params["f_experiment"][0]
            if "f_experiment" in params
            else params["f_participant"][0]
        ] = params

    session_link = links["session_id"]
    assert session_link["op_session_id"] == ["equals"]
    assert session_link["f_session_id"] == [str(trace.session.external_id)]

    experiment_link = links["experiment"]
    assert experiment_link["op_experiment"] == ["any of"]
    assert experiment_link["f_experiment"] == [str(trace.experiment_id)]

    participant_link = links["participant"]
    assert participant_link["op_participant"] == ["equals"]
    assert participant_link["f_participant"] == [trace.participant.identifier]


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
