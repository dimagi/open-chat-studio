import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.teams.backends import CHAT_VIEWER_GROUP, SUPER_ADMIN_GROUP, add_user_to_team, make_user_team_owner
from apps.teams.models import Invitation
from apps.teams.views.members_views import filter_member_rows, get_member_rows
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def team():
    return TeamFactory()


@pytest.mark.django_db()
def test_get_member_rows_merges_members_and_pending_invitations_only(team):
    admin = UserFactory(email="admin@example.org")
    make_user_team_owner(team, admin)
    member = UserFactory(email="member@example.org")
    add_user_to_team(team, member)

    pending = Invitation.objects.create(team=team, email="pending@example.org", invited_by=admin)
    accepted = Invitation.objects.create(
        team=team, email="accepted@example.org", invited_by=admin, is_accepted=True, accepted_by=member
    )

    rows = get_member_rows(team)
    ids = {row["id"] for row in rows}

    assert f"invite-{pending.id}" in ids
    assert f"invite-{accepted.id}" not in ids, "an already-accepted invitation must not reappear as a pending row"
    assert len(rows) == 3  # admin, member, pending invite


@pytest.mark.django_db()
def test_filter_member_rows_by_search(team):
    make_user_team_owner(team, UserFactory(email="alice@example.org", first_name="Alice"))
    make_user_team_owner(team, UserFactory(email="bob@example.org", first_name="Bob"))

    rows = get_member_rows(team)
    filtered = filter_member_rows(rows, {"search": "alice"})
    assert len(filtered) == 1
    assert filtered[0]["email"] == "alice@example.org"

    filtered = filter_member_rows(rows, {"search": "example.org"})
    assert len(filtered) == 2


@pytest.mark.django_db()
def test_filter_member_rows_by_role(team):
    admin_user = UserFactory(email="admin@example.org")
    make_user_team_owner(team, admin_user)
    member_user = UserFactory(email="member@example.org")
    add_user_to_team(team, member_user, groups=[CHAT_VIEWER_GROUP])

    rows = get_member_rows(team)
    filtered = filter_member_rows(rows, {"role": SUPER_ADMIN_GROUP})
    assert [row["email"] for row in filtered] == ["admin@example.org"]

    filtered = filter_member_rows(rows, {"role": CHAT_VIEWER_GROUP})
    assert [row["email"] for row in filtered] == ["member@example.org"]


@pytest.mark.django_db()
def test_filter_member_rows_by_status(team):
    admin = UserFactory(email="admin@example.org")
    make_user_team_owner(team, admin)
    Invitation.objects.create(team=team, email="pending@example.org", invited_by=admin)

    rows = get_member_rows(team)
    assert [row["status_kind"] for row in filter_member_rows(rows, {"status": "active"})] == ["active"]
    assert [row["status_kind"] for row in filter_member_rows(rows, {"status": "invited"})] == ["invited"]


@pytest.mark.django_db()
def test_send_invitation_returns_members_section_fragment_with_new_invite(client, team):
    admin = UserFactory(email="admin@example.org")
    make_user_team_owner(team, admin)
    client.force_login(admin)
    viewer_group = Group.objects.get(name=CHAT_VIEWER_GROUP)

    response = client.post(
        reverse("single_team:send_invitation", args=[team.slug]),
        {"email": "newperson@example.org", "groups": [viewer_group.pk]},
    )

    assert response.status_code == 200
    assert Invitation.objects.filter(team=team, email="newperson@example.org", is_accepted=False).exists()
    assert b"members-section" in response.content
    # The response re-renders the whole #members-section root, so the form that
    # triggered it must replace that element rather than nest a duplicate inside it.
    assert b'hx-swap="outerHTML"' in response.content


@pytest.mark.django_db()
def test_members_table_view_applies_search_filter(client, team):
    admin = UserFactory(email="admin@example.org")
    make_user_team_owner(team, admin)
    add_user_to_team(team, UserFactory(email="someoneelse@example.org"))
    client.force_login(admin)

    response = client.get(reverse("single_team:members_table", args=[team.slug]), {"search": "someoneelse"})
    assert response.status_code == 200
    assert b"someoneelse@example.org" in response.content
    assert b"admin@example.org" not in response.content


@pytest.mark.django_db()
def test_members_table_initial_load_includes_count_and_wrapper(client, team):
    """The initial `hx-trigger="load"` request (no page/sort param) needs the full
    fragment -- the "N of M" count text and its wrapper -- not just the bare table."""
    admin = UserFactory(email="admin@example.org")
    make_user_team_owner(team, admin)
    client.force_login(admin)

    response = client.get(reverse("single_team:members_table", args=[team.slug]))
    assert response.status_code == 200
    assert b"of" in response.content
    assert b'class="overflow-x-auto"' in response.content


@pytest.mark.django_db()
def test_members_table_pagination_request_returns_bare_table_only(client, team):
    """A pagination click targets `closest div.table-container` with an outerHTML swap
    (table/tailwind_js_pagination.html), so a `?page=` request must return exactly that
    container -- not the count text and wrapper -- or each click nests another copy of
    them inside the previous one."""
    admin = UserFactory(email="admin@example.org")
    make_user_team_owner(team, admin)
    client.force_login(admin)

    response = client.get(
        reverse("single_team:members_table", args=[team.slug]), {"page": "1"}, headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    assert b"overflow-x-auto" not in response.content
    assert b'class="table-container"' in response.content


@pytest.mark.django_db()
def test_members_table_non_htmx_pagination_request_returns_full_page(client, team):
    """The pagination link now carries a real `href` (see #4463), so a modified click
    (Ctrl/Cmd-click, middle-click) opens `?page=` as a genuine full-page navigation, not
    an htmx request. `get_template_names` branched on the query string alone, so a request
    like this used to get the bare fragment -- no nav, no page chrome -- instead of the
    full page it actually needs."""
    admin = UserFactory(email="admin@example.org")
    make_user_team_owner(team, admin)
    client.force_login(admin)

    response = client.get(reverse("single_team:members_table", args=[team.slug]), {"page": "1"})

    assert response.status_code == 200
    assert b"overflow-x-auto" in response.content


@pytest.mark.django_db()
def test_members_table_next_page_link_uses_morph_swap(client, team):
    """The original motivating case for #4425: clicking the pagination "next" link
    self-swaps `closest div.table-container`, destroying the link and dropping keyboard
    focus to <body>. `hx-swap="morph"` (table/tailwind_js_pagination.html) fixes this at
    the shared-template level, for every table that renders through it."""
    admin = UserFactory(email="admin@example.org")
    make_user_team_owner(team, admin)
    for i in range(30):
        add_user_to_team(team, UserFactory(email=f"member{i}@example.org"))
    client.force_login(admin)

    response = client.get(reverse("single_team:members_table", args=[team.slug]), {"page": "1"})

    assert response.status_code == 200
    assert b'hx-ext="morph"' in response.content
    assert b'hx-swap="morph"' in response.content
