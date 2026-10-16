"""The sessions table shows each session's annotation queues as badges when human annotations are on."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from waffle.testutils import override_flag

from apps.human_annotations.models import AnnotationItemStatus
from apps.human_annotations.prefetch import attach_session_annotation_items
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.human_annotations import AnnotationItemFactory, AnnotationQueueFactory

COLUMN_HEADER = "Annotation Queues"


@pytest.fixture()
def logged_in_team(client, team_with_users):
    """The team whose admin the test client is logged in as."""
    client.force_login(team_with_users.members.first())
    return team_with_users


def _session(team):
    """A session whose chat, experiment and participant belong to the given team."""
    return ExperimentSessionFactory.create(team=team, chat__team=team, experiment__team=team, participant__team=team)


def _all_sessions(client, team):
    """The rendered all-sessions table for the team."""
    return client.get(reverse("chatbots:all_sessions_list", args=[team.slug])).content.decode()


@pytest.mark.parametrize("scope", [pytest.param("all", id="all-sessions"), pytest.param("chatbot", id="per-chatbot")])
@pytest.mark.django_db()
def test_sessions_table_shows_a_badge_per_queue_with_the_item_status(client, logged_in_team, scope):
    """Each queue the session is in appears as 'Queue · Status', on both sessions tables."""
    team = logged_in_team
    session = _session(team)
    AnnotationItemFactory.create(
        queue=AnnotationQueueFactory.create(team=team, name="Safety QA"),
        team=team,
        session=session,
        status=AnnotationItemStatus.COMPLETED,
    )
    tone_queue = AnnotationQueueFactory.create(team=team, name="Tone")
    AnnotationItemFactory.create(queue=tone_queue, team=team, session=session)

    if scope == "all":
        url = reverse("chatbots:all_sessions_list", args=[team.slug])
    else:
        url = reverse("chatbots:sessions-list", kwargs={"team_slug": team.slug, "experiment_id": session.experiment_id})
    with override_flag("flag_human_annotations", active=True):
        html = client.get(url).content.decode()

    assert COLUMN_HEADER in html
    assert "Safety QA · Completed" in html
    assert "Tone · Pending" in html


@pytest.mark.django_db()
def test_annotation_queues_column_is_hidden_without_the_flag(client, logged_in_team):
    """Teams without human annotations never see the column."""
    team = logged_in_team
    queue = AnnotationQueueFactory.create(team=team, name="Safety QA")
    AnnotationItemFactory.create(queue=queue, team=team, session=_session(team))

    with override_flag("flag_human_annotations", active=False):
        html = _all_sessions(client, team)

    assert COLUMN_HEADER not in html
    assert "Safety QA" not in html


@pytest.mark.django_db()
def test_participant_sessions_table_never_shows_the_column(client, logged_in_team):
    """The participant details page keeps its own column set."""
    team = logged_in_team
    session = _session(team)
    queue = AnnotationQueueFactory.create(team=team, name="Safety QA")
    AnnotationItemFactory.create(queue=queue, team=team, session=session)
    url = reverse(
        "chatbots:participant_sessions_list",
        kwargs={"team_slug": team.slug, "participant_id": session.participant_id},
    )

    with override_flag("flag_human_annotations", active=True):
        html = client.get(url).content.decode()

    assert COLUMN_HEADER not in html
    assert "Safety QA" not in html


@pytest.mark.django_db()
def test_queue_badges_cost_one_query_regardless_of_row_count(client, logged_in_team):
    """Turning the column on adds a single query per page, however many rows the page has."""
    team = logged_in_team
    queue = AnnotationQueueFactory.create(team=team, name="Safety QA")

    def queries_added_by_the_column():
        with override_flag("flag_human_annotations", active=False):
            _all_sessions(client, team)
            with CaptureQueriesContext(connection) as without_column:
                _all_sessions(client, team)
        with override_flag("flag_human_annotations", active=True):
            _all_sessions(client, team)
            with CaptureQueriesContext(connection) as with_column:
                _all_sessions(client, team)
        return len(with_column) - len(without_column)

    AnnotationItemFactory.create(queue=queue, team=team, session=_session(team))
    one_row = queries_added_by_the_column()
    for _ in range(3):
        AnnotationItemFactory.create(queue=queue, team=team, session=_session(team))

    assert queries_added_by_the_column() == one_row == 1


@pytest.mark.django_db()
def test_attaching_items_to_an_empty_page_runs_no_query(django_assert_num_queries):
    """An empty page returns an empty list without touching the database."""
    with django_assert_num_queries(0):
        assert attach_session_annotation_items([]) == []
