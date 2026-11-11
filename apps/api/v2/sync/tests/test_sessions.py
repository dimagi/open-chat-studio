"""GET /api/v2/sessions/: incremental, team-scoped session sync (ADR-0072)."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession
from apps.teams.backends import add_user_to_team
from apps.utils.factories.annotations import TagFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient

URL = reverse("api:v2:session-list")
BASE = timezone.now() - timedelta(days=1)


@pytest.fixture()
def team():
    return TeamWithUsersFactory.create()


@pytest.fixture()
def client(team):
    return ApiTestClient(team.members.first(), team)


def _session(team, minutes=0, **kwargs):
    """A session last updated ``minutes`` after BASE, well outside the settle window."""
    kwargs.setdefault("experiment", ExperimentFactory.create(team=team))
    session = ExperimentSessionFactory.create(team=team, **kwargs)
    ExperimentSession.objects.filter(pk=session.pk).update(updated_at=BASE + timedelta(minutes=minutes))
    return session


def _ids(response):
    assert response.status_code == 200, response.content
    return [row["id"] for row in response.json()["results"]]


@pytest.mark.django_db()
def test_unauthenticated_is_refused():
    assert APIClient().get(URL).status_code == 401


@pytest.mark.django_db()
def test_returns_the_team_s_sessions_in_update_order(team, client):
    later = _session(team, minutes=2)
    earlier = _session(team, minutes=1)
    _session(TeamWithUsersFactory.create())

    response = client.get(URL)

    assert _ids(response) == [str(earlier.external_id), str(later.external_id)]
    row = response.json()["results"][0]
    assert row == {
        "id": str(earlier.external_id),
        "chatbot_id": str(earlier.experiment.public_id),
        "participant_id": str(earlier.participant.public_id),
        "platform": earlier.platform,
        "status": earlier.status,
        "state": earlier.state,
        "tags": [],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "ended_at": None,
    }


@pytest.mark.django_db()
def test_cursor_resumes_and_is_kept_once_caught_up(team, client):
    sessions = [_session(team, minutes=i) for i in range(3)]

    first = client.get(URL, {"limit": 2}).json()
    second = client.get(URL, {"limit": 2, "cursor": first["cursor"]}).json()
    caught_up = client.get(URL, {"limit": 2, "cursor": second["cursor"]}).json()

    assert [row["id"] for row in first["results"] + second["results"]] == [str(s.external_id) for s in sessions]
    assert (first["has_more"], second["has_more"]) == (True, False)
    assert caught_up == {"cursor": second["cursor"], "has_more": False, "results": []}


@pytest.mark.django_db()
def test_rows_sharing_a_timestamp_split_cleanly_across_pages(team, client):
    sessions = [_session(team) for _ in range(5)]

    seen, cursor = [], None
    for _ in range(5):
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = client.get(URL, params).json()
        seen += [row["id"] for row in page["results"]]
        cursor = page["cursor"]
        if not page["has_more"]:
            break

    assert sorted(seen) == sorted(str(s.external_id) for s in sessions)
    assert len(seen) == len(set(seen))


@pytest.mark.django_db()
def test_an_edited_session_comes_back_on_the_next_pull(team, client):
    edited = _session(team, minutes=1)
    _session(team, minutes=2)
    cursor = client.get(URL).json()["cursor"]

    edited.state = {"step": 2}
    edited.save()
    ExperimentSession.objects.filter(pk=edited.pk).update(updated_at=BASE + timedelta(minutes=3))

    assert _ids(client.get(URL, {"cursor": cursor})) == [str(edited.external_id)]


@pytest.mark.django_db()
def test_sessions_inside_the_settle_window_are_held_back(team, client):
    settled = _session(team)
    fresh = ExperimentSessionFactory.create(team=team, experiment=settled.experiment)

    first = client.get(URL).json()
    ExperimentSession.objects.filter(pk=fresh.pk).update(updated_at=timezone.now() - timedelta(minutes=5))
    second = client.get(URL, {"cursor": first["cursor"]})

    assert [row["id"] for row in first["results"]] == [str(settled.external_id)]
    assert _ids(second) == [str(fresh.external_id)]


@pytest.mark.django_db()
def test_chatbot_filter(team, client):
    wanted = _session(team)
    _session(team)

    assert _ids(client.get(URL, {"chatbot": str(wanted.experiment.public_id)})) == [str(wanted.external_id)]


@pytest.mark.django_db()
def test_participant_filter(team, client):
    wanted = _session(team)
    _session(team)

    assert _ids(client.get(URL, {"participant": str(wanted.participant.public_id)})) == [str(wanted.external_id)]


@pytest.mark.django_db()
def test_platform_filter(team, client):
    wanted = _session(team)
    other = _session(team)
    ExperimentSession.objects.filter(pk=wanted.pk).update(platform=ChannelPlatform.WHATSAPP)
    ExperimentSession.objects.filter(pk=other.pk).update(platform=ChannelPlatform.TELEGRAM)

    assert _ids(client.get(URL, {"platform": "whatsapp"})) == [str(wanted.external_id)]


@pytest.mark.django_db()
def test_tags_filter_matches_any_and_lists_the_tags(team, client):
    tagged = _session(team)
    _session(team)
    tag = TagFactory.create(team=team, name="billing")
    tagged.chat.add_tag(tag, team=team)
    ExperimentSession.objects.filter(pk=tagged.pk).update(updated_at=BASE)

    response = client.get(URL, {"tags": ["billing", "refund"]})

    assert _ids(response) == [str(tagged.external_id)]
    assert response.json()["results"][0]["tags"] == ["billing"]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("scope", "status"),
    [
        pytest.param("sessions:read", 200, id="sessions-read"),
        pytest.param("chatbots:read", 403, id="other-scope"),
    ],
)
def test_oauth_needs_the_sessions_read_scope(team, scope, status):
    client = ApiTestClient(team.members.first(), team, auth_method="oauth", scopes=[scope])

    assert client.get(URL).status_code == status


@pytest.mark.django_db()
@pytest.mark.parametrize("all_data", [pytest.param(False, id="listed-chatbots"), pytest.param(True, id="all-data")])
def test_machine_token_reads_only_the_chatbots_it_reaches(team, all_data):
    listed = _session(team)
    unlisted = _session(team)
    client = ApiTestClient(
        team.members.first(),
        team,
        auth_method="oauth_client_credentials",
        scopes=["sessions:read"],
        allowed_chatbots=[listed.experiment],
        all_data=all_data,
    )

    expected = {str(listed.external_id), str(unlisted.external_id)} if all_data else {str(listed.external_id)}
    assert set(_ids(client.get(URL))) == expected


@pytest.mark.django_db()
def test_invalid_cursor_is_a_400(client):
    assert client.get(URL, {"cursor": "not-a-cursor"}).status_code == 400


@pytest.mark.django_db()
def test_queries_per_page_do_not_grow_with_the_page(team, client):
    tag = TagFactory.create(team=team)

    def queries_for(count):
        for session in [_session(team) for _ in range(count)]:
            session.chat.add_tag(tag, team=team)
        ExperimentSession.objects.filter(team=team).update(updated_at=BASE)
        with CaptureQueriesContext(connection) as queries:
            assert client.get(URL, {"limit": 100}).status_code == 200
        return len(queries)

    assert queries_for(2) == queries_for(6)


@pytest.mark.django_db()
def test_a_member_without_session_access_is_refused(team):
    user = UserFactory.create()
    add_user_to_team(team, user)

    assert ApiTestClient(user, team).get(URL).status_code == 403


@pytest.mark.django_db()
def test_a_read_only_key_may_read(team):
    _session(team)

    assert ApiTestClient(team.members.first(), team, read_only=True).get(URL).status_code == 200
