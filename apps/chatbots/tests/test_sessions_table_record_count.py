"""The sessions table shows how many records match, so a filter's effect is visible.

See https://github.com/dimagi/open-chat-studio/issues/4186. Only the sessions table opts in
(via `ChatbotSessionsTable.show_record_count`); every other table renders unchanged.
"""

import pytest
from django.urls import reverse

from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineFactory

COUNT_MARKER = 'data-cy="table-record-count"'


def normalised(response):
    return " ".join(response.content.decode().split())


@pytest.fixture()
def logged_in_team(client, team_with_users):
    client.force_login(team_with_users.members.first())
    return team_with_users


def make_sessions(team, experiment, count):
    for _ in range(count):
        ExperimentSessionFactory.create(team=team, experiment=experiment, participant__team=team)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("session_count", "expected"),
    [
        pytest.param(1, "1 record <", id="singular"),
        pytest.param(3, "3 records <", id="plural"),
    ],
)
def test_sessions_table_shows_the_record_count(client, logged_in_team, session_count, expected):
    experiment = ExperimentFactory.create(team=logged_in_team)
    make_sessions(logged_in_team, experiment, session_count)

    url = reverse("chatbots:sessions-list", kwargs={"team_slug": logged_in_team.slug, "experiment_id": experiment.id})
    response = client.get(url)

    assert response.status_code == 200
    assert COUNT_MARKER in normalised(response)
    assert expected in normalised(response)


@pytest.mark.django_db()
def test_record_count_reflects_an_applied_filter(client, logged_in_team):
    """The count is the point of the issue: it must track the filtered set, not the whole table."""
    experiment = ExperimentFactory.create(team=logged_in_team)
    make_sessions(logged_in_team, experiment, 3)
    matching = ExperimentSessionFactory.create(
        team=logged_in_team, experiment=experiment, participant__team=logged_in_team
    )

    url = reverse("chatbots:sessions-list", kwargs={"team_slug": logged_in_team.slug, "experiment_id": experiment.id})
    response = client.get(url, {"f_session_id": str(matching.external_id), "op_session_id": "equals"})

    assert response.status_code == 200
    assert list(response.context_data["table"].data.data) == [matching]
    assert "1 record <" in normalised(response)


@pytest.mark.django_db()
def test_no_record_count_when_no_sessions_match(client, logged_in_team):
    """An empty table already says so via `empty_text`; don't also claim "0 records"."""
    experiment = ExperimentFactory.create(team=logged_in_team)

    url = reverse("chatbots:sessions-list", kwargs={"team_slug": logged_in_team.slug, "experiment_id": experiment.id})
    response = client.get(url)

    assert response.status_code == 200
    assert COUNT_MARKER not in normalised(response)


@pytest.mark.django_db()
def test_other_tables_are_unchanged(client, logged_in_team):
    """Only the sessions table opts in; the pipelines table must render exactly as before."""
    PipelineFactory.create(team=logged_in_team)

    response = client.get(reverse("pipelines:table", kwargs={"team_slug": logged_in_team.slug}))

    assert response.status_code == 200
    assert COUNT_MARKER not in normalised(response)
