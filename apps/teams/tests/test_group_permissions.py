import pytest
from django.urls import reverse

from apps.teams.backends import (
    ADD,
    ALL,
    ANNOTATION_REVIEWER_GROUP,
    CHANGE,
    CHAT_VIEWER_GROUP,
    CHATBOT_ADMIN_GROUP,
    CONTENT_TYPES,
    EVENT_ADMIN_GROUP,
    GROUPS,
    SUPER_ADMIN_GROUP,
    TEAM_ADMIN_GROUP,
    VIEW,
    AppPermSetDef,
    CustomPermissionSetDef,
    GroupDef,
    add_user_to_team,
    make_user_team_owner,
)
from apps.teams.group_permissions import get_group_permission_tables
from apps.teams.utils import section_url
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


def _table(group_name):
    return next(table for table in get_group_permission_tables() if table.group == group_name)


def _row(group_name, area):
    return [(row.resources, row.actions) for row in _table(group_name).rows if row.area == area]


def test_one_table_per_group_in_definition_order():
    assert [table.group for table in get_group_permission_tables()] == [group_def.name for group_def in GROUPS]


@pytest.mark.parametrize(
    ("group_name", "area", "expected"),
    [
        pytest.param(SUPER_ADMIN_GROUP, "Teams", [((), frozenset(ALL))], id="whole-app-grant"),
        pytest.param(CHAT_VIEWER_GROUP, "Chat", [((), frozenset([VIEW]))], id="whole-app-view-only"),
        pytest.param(
            CHAT_VIEWER_GROUP, "Chatbots", [(("Experiment sessions",), frozenset([VIEW]))], id="single-model-grant"
        ),
        pytest.param(
            CHATBOT_ADMIN_GROUP,
            "Annotations",
            [
                (("Tags",), frozenset([VIEW])),
                (("Custom tagged items", "User comments"), frozenset(ALL)),
            ],
            id="models-split-by-actions",
        ),
        pytest.param(
            ANNOTATION_REVIEWER_GROUP,
            "Human annotations",
            [
                (("Annotation queues", "Annotation queue aggregates"), frozenset([VIEW])),
                (("Annotation items",), frozenset([VIEW, CHANGE])),
                (("Annotations",), frozenset([ADD])),
            ],
            id="mixed-model-grants",
        ),
        pytest.param(
            TEAM_ADMIN_GROUP, "Notifications", [(("Notification channels",), frozenset(ALL))], id="readable-app-name"
        ),
    ],
)
def test_rows(group_name, area, expected):
    assert _row(group_name, area) == expected


def test_rows_sorted_by_area():
    areas = [row.area for row in _table(SUPER_ADMIN_GROUP).rows]
    assert areas == sorted(areas)


def test_group_without_access_to_an_area_has_no_row_for_it():
    assert [row.area for row in _table(EVENT_ADMIN_GROUP).rows] == ["Events"]


@pytest.mark.parametrize("group_def", [pytest.param(group_def, id=group_def.name) for group_def in GROUPS])
def test_rows_cover_exactly_the_defined_permissions(group_def):
    defined = {
        (perm_def.app_label, codename)
        for perm_def in group_def.permission_defs
        if not isinstance(perm_def, CustomPermissionSetDef)
        for codename in perm_def.codenames
    }
    shown = {
        (row.app_label, f"{action}_{model}")
        for row in _table(group_def.name).rows
        for model in row.models
        for action in row.actions
    }
    assert shown == defined


def test_row_cells_follow_column_order():
    row = next(row for row in _table(CHAT_VIEWER_GROUP).rows if row.area == "Chat")
    assert row.cells == [True, False, False, False]


@pytest.mark.parametrize(
    ("group_name", "expected"),
    [
        pytest.param(
            CHATBOT_ADMIN_GROUP,
            ["Invite experiment participants", "Download experiment chats"],
            id="custom-permissions",
        ),
        pytest.param(EVENT_ADMIN_GROUP, [], id="none"),
    ],
)
def test_other_permissions(group_name, expected):
    assert _table(group_name).other_permissions == expected


def test_every_app_with_models_has_a_readable_area_name():
    super_admin = _table(SUPER_ADMIN_GROUP)
    areas = {row.app_label: row.area for row in super_admin.rows}
    assert set(areas) == {app_label for app_label, models in CONTENT_TYPES.items() if models}
    assert all("_" not in area for area in areas.values())


def test_group_def_with_an_app_grant():
    tables = get_group_permission_tables([GroupDef("Custom", [AppPermSetDef("trace", [VIEW])])])
    assert [(row.area, row.resources, row.cells) for row in tables[0].rows] == [
        ("Traces", (), [True, False, False, False])
    ]


@pytest.fixture()
def team():
    return TeamFactory()


@pytest.mark.django_db()
@pytest.mark.parametrize("is_admin", [pytest.param(True, id="admin"), pytest.param(False, id="member")])
def test_members_section_shows_group_permissions(client, team, is_admin):
    user = UserFactory()
    if is_admin:
        make_user_team_owner(team, user)
    else:
        add_user_to_team(team, user)
    client.force_login(user)

    response = client.get(section_url(team_slug=team.slug, section_key="members"))

    assert response.status_code == 200
    assert [table.group for table in response.context["group_permission_tables"]] == [g.name for g in GROUPS]
    assert b'id="group-permissions"' in response.content


@pytest.mark.django_db()
def test_membership_page_shows_group_permissions(client, team):
    admin = UserFactory()
    make_user_team_owner(team, admin)
    membership = add_user_to_team(team, UserFactory())
    client.force_login(admin)

    response = client.get(reverse("single_team:team_membership_details", args=[team.slug, membership.pk]))

    assert response.status_code == 200
    assert b'id="group-permissions"' in response.content
