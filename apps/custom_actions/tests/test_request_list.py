from datetime import timedelta

import pytest
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.teams.backends import CHATBOT_ADMIN_GROUP, SUPER_ADMIN_GROUP, TEAM_ADMIN_GROUP
from apps.trace.models import CustomActionRequest
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.traces import CustomActionRequestFactory, TraceFactory
from apps.utils.tests.team_helpers import member_with_groups

pytestmark = pytest.mark.django_db

ROW = 'data-testid="custom-action-request-row"'


@pytest.fixture()
def custom_action(team):
    return CustomActionFactory.create(team=team)


@pytest.fixture()
def trace(team):
    return TraceFactory.create(team=team)


@pytest.fixture()
def admin_client(client, team):
    client.force_login(member_with_groups(team=team, group_names=[SUPER_ADMIN_GROUP]))
    return client


def requests_url(custom_action):
    return reverse("custom_actions:requests", args=[custom_action.team.slug, custom_action.pk])


def create_requests(custom_action, trace, count, **kwargs):
    """Create `count` requests for `custom_action`, numbered in the order they were made."""
    now = timezone.now()
    for i in range(count):
        log = CustomActionRequestFactory.create(
            trace=trace, custom_action=custom_action, url=f"https://api.example.com/item/{i}", **kwargs
        )
        CustomActionRequest.objects.filter(pk=log.pk).update(created_at=now - timedelta(minutes=count - i))


def test_lists_requests_newest_first(admin_client, custom_action, trace):
    create_requests(custom_action, trace, 3, method="POST", status_code=502, duration_ms=731)

    response = admin_client.get(requests_url(custom_action))

    assert response.status_code == 200
    content = response.content.decode()
    assert content.count(ROW) == 3
    positions = [content.index(f"https://api.example.com/item/{i}") for i in (2, 1, 0)]
    assert positions == sorted(positions)
    for text in ["POST", "502", "731 ms", "badge-error", "<time datetime="]:
        assert text in content


def test_links_each_request_to_its_trace(admin_client, custom_action, trace):
    create_requests(custom_action, trace, 1)

    response = admin_client.get(requests_url(custom_action))

    trace_url = reverse("trace:trace_detail", args=[trace.team.slug, trace.pk])
    assert f'href="{trace_url}"' in response.content.decode()


def test_shows_only_the_latest_fifty_requests(admin_client, custom_action, trace):
    create_requests(custom_action, trace, 51)

    response = admin_client.get(requests_url(custom_action))

    content = response.content.decode()
    assert content.count(ROW) == 50
    assert "https://api.example.com/item/50<" in content
    assert "https://api.example.com/item/0<" not in content


def test_shows_an_empty_state_without_requests(admin_client, custom_action):
    response = admin_client.get(requests_url(custom_action))

    assert response.status_code == 200
    content = response.content.decode()
    assert ROW not in content
    assert "No requests logged yet." in content


def test_lists_only_this_actions_requests(admin_client, custom_action, trace):
    create_requests(custom_action, trace, 1)
    other_team = TeamFactory.create()
    CustomActionRequestFactory.create(
        trace=TraceFactory.create(team=other_team),
        custom_action=CustomActionFactory.create(team=other_team),
        url="https://other-team.example.com/secret",
    )
    CustomActionRequestFactory.create(
        trace=trace,
        custom_action=CustomActionFactory.create(team=custom_action.team),
        url="https://sibling-action.example.com/",
    )

    content = admin_client.get(requests_url(custom_action)).content.decode()

    assert content.count(ROW) == 1
    assert "other-team.example.com" not in content
    assert "sibling-action.example.com" not in content


def test_another_teams_action_is_not_found(admin_client, team):
    other_action = CustomActionFactory.create(team=TeamFactory.create())

    response = admin_client.get(reverse("custom_actions:requests", args=[team.slug, other_action.pk]))

    assert response.status_code == 404


@pytest.mark.parametrize(
    "group_name",
    [
        pytest.param(TEAM_ADMIN_GROUP, id="no-request-permission"),
        pytest.param(CHATBOT_ADMIN_GROUP, id="no-custom-action-permission"),
    ],
)
def test_requires_custom_action_and_request_permissions(client, custom_action, group_name):
    client.force_login(member_with_groups(team=custom_action.team, group_names=[group_name]))

    response = client.get(requests_url(custom_action))

    assert response.status_code == 403


def test_hides_the_trace_link_without_trace_permission(client, custom_action, trace):
    create_requests(custom_action, trace, 1)
    group = Group.objects.create(name="Request viewers")
    group.permissions.set(
        Permission.objects.filter(
            content_type__app_label__in=["custom_actions", "trace"],
            codename__in=["view_customaction", "view_customactionrequest"],
        )
    )
    client.force_login(MembershipFactory.create(team=custom_action.team, groups=[group]).user)

    response = client.get(requests_url(custom_action))

    content = response.content.decode()
    assert content.count(ROW) == 1
    assert reverse("trace:trace_detail", args=[trace.team.slug, trace.pk]) not in content


def test_query_count_does_not_grow_with_requests(admin_client, custom_action, trace):
    create_requests(custom_action, trace, 1)
    admin_client.get(requests_url(custom_action))  # warm the caches the first request fills
    with CaptureQueriesContext(connection) as one_request:
        admin_client.get(requests_url(custom_action))

    other_trace = TraceFactory.create(team=custom_action.team)
    create_requests(custom_action, other_trace, 5)
    with CaptureQueriesContext(connection) as six_requests:
        admin_client.get(requests_url(custom_action))

    assert len(six_requests) == len(one_request)


@pytest.mark.parametrize(
    ("group_name", "lists_requests"),
    [
        pytest.param(SUPER_ADMIN_GROUP, True, id="with-request-permission"),
        pytest.param(TEAM_ADMIN_GROUP, False, id="without-request-permission"),
    ],
)
def test_edit_page_loads_the_request_list(client, custom_action, group_name, lists_requests):
    client.force_login(member_with_groups(team=custom_action.team, group_names=[group_name]))

    response = client.get(reverse("custom_actions:edit", args=[custom_action.team.slug, custom_action.pk]))

    assert response.status_code == 200
    assert (f'hx-get="{requests_url(custom_action)}"' in response.content.decode()) is lists_requests
