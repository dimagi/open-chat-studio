"""The sessions table shows how many records match, so a filter's effect is visible.

See https://github.com/dimagi/open-chat-studio/issues/4186. Only the sessions table opts in
(via `ChatbotSessionsTable.show_record_count`); every other table renders unchanged.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django_tables2 import RequestConfig

from apps.chatbots.tables import ChatbotSessionsTable
from apps.experiments.models import ExperimentSession
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


def _configured_table(request, team, experiment, *, per_page=25):
    """Build and configure the sessions table the way the view does."""
    queryset = ExperimentSession.objects.get_table_queryset(team, experiment.id)
    table = ChatbotSessionsTable(queryset)
    RequestConfig(request, paginate={"per_page": per_page}).configure(table)
    return table


def _count_queries(captured):
    return [q["sql"] for q in captured if "COUNT(*)" in q["sql"]]


@pytest.mark.django_db()
def test_paginating_issues_the_count_before_anything_reads_it(rf, logged_in_team):
    """The paginator counts whether or not the count is ever displayed.

    `Paginator.validate_number` reads `num_pages`, which reads `count`, so configuring a
    paginated table always issues the COUNT -- before a template could ask for it. That is
    what makes displaying the record count free.
    """
    experiment = ExperimentFactory.create(team=logged_in_team)
    make_sessions(logged_in_team, experiment, 3)

    with CaptureQueriesContext(connection) as ctx:
        table = _configured_table(rf.get("/"), logged_in_team, experiment)

    assert len(_count_queries(ctx.captured_queries)) == 1
    # The COUNT was issued and memoised by the pagination machinery alone -- no template
    # and no view code read `paginator.count` to make that happen.
    assert "count" in table.paginator.__dict__


@pytest.mark.django_db()
def test_reading_paginator_count_issues_no_further_queries(rf, logged_in_team):
    """Reading the count -- repeatedly -- costs nothing once the table is paginated."""
    experiment = ExperimentFactory.create(team=logged_in_team)
    make_sessions(logged_in_team, experiment, 3)
    table = _configured_table(rf.get("/"), logged_in_team, experiment)

    with CaptureQueriesContext(connection) as ctx:
        first = table.paginator.count
        second = table.paginator.count

    assert first == second == 3
    assert ctx.captured_queries == []


@pytest.mark.django_db()
@pytest.mark.parametrize("show_record_count", [True, False], ids=["count-shown", "count-hidden"])
def test_showing_the_record_count_adds_no_query(client, logged_in_team, monkeypatch, show_record_count):
    """The full request issues exactly one COUNT either way -- the paginator's own.

    Pinning the absolute number rather than diffing two requests: a diff would still pass if
    both sides grew a query, and is sensitive to per-process caches warming mid-test.
    """
    monkeypatch.setattr(ChatbotSessionsTable, "show_record_count", show_record_count, raising=False)
    experiment = ExperimentFactory.create(team=logged_in_team)
    make_sessions(logged_in_team, experiment, 3)
    url = reverse("chatbots:sessions-list", kwargs={"team_slug": logged_in_team.slug, "experiment_id": experiment.id})
    client.get(url)  # settle per-process caches (Site lookup, permissions)

    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)

    assert len(_count_queries(ctx.captured_queries)) == 1
    assert (COUNT_MARKER in response.content.decode()) is show_record_count
