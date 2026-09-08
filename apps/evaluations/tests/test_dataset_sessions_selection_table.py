from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.annotations.prefetch import attach_chat_tagged_items
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def user(team_with_users):
    return team_with_users.members.first()


@pytest.fixture()
def client_with_user(user):
    c = Client()
    c.force_login(user)
    return c


def _table_url(team):
    return reverse("evaluations:dataset_sessions_selection_list", args=[team.slug])


@pytest.mark.django_db()
def test_session_selection_table_renders_session_id_and_tags(client_with_user, team_with_users):
    """The Session ID and Tags columns added to EvaluationSessionsSelectionTable must actually
    render the session's external_id and its chat's tags, not just be declared on the table."""
    session = ExperimentSessionFactory(team=team_with_users)
    session.chat.create_and_add_tag("qa-review", team=team_with_users, tag_category="")

    response = client_with_user.get(_table_url(team_with_users))

    assert response.status_code == 200
    assert str(session.external_id).encode() in response.content
    assert b"qa-review" in response.content


@pytest.mark.django_db()
def test_session_selection_table_tag_prefetch_is_bounded_to_current_page(client_with_user, team_with_users):
    """Pins down the ordering requirement documented on ChatTagPrefetchTableMixin: the tag
    prefetch must run after RequestConfig.configure() has already sliced the table to one page,
    or it silently regresses into an unbounded, full-queryset lookup.

    With more sessions than fit on a page, a correctly-ordered prefetch only ever sees the
    page's rows -- so asserting on the length of what it was called with pins that ordering
    down directly, independent of how the query itself is written.
    """
    per_page = 25
    total_sessions = per_page + 5
    for _ in range(total_sessions):
        session = ExperimentSessionFactory(team=team_with_users)
        session.chat.create_and_add_tag("qa-review", team=team_with_users, tag_category="")

    with patch(
        "apps.evaluations.views.dataset_views.attach_chat_tagged_items", wraps=attach_chat_tagged_items
    ) as mock_attach:
        response = client_with_user.get(_table_url(team_with_users))

    assert response.status_code == 200
    mock_attach.assert_called_once()
    (rows,), _ = mock_attach.call_args
    assert len(rows) == per_page
