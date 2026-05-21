import pytest
from django.urls import reverse

from apps.teams.backends import (
    CHAT_VIEWER_GROUP,
    CHATBOT_ADMIN_GROUP,
    add_user_to_team,
    create_default_groups,
)
from apps.utils.factories.files import FileFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


@pytest.mark.django_db()
@pytest.mark.parametrize("group_name", [CHATBOT_ADMIN_GROUP, CHAT_VIEWER_GROUP])
def test_file_view_accessible_to_group(client, group_name):
    """Regression test for #925: members of these groups can download a team File.

    FileView is gated by `files.view_file`. Without that permission a logged-in
    member who can generate a chat export (Chatbot Admin) or view a session
    attachment (Chat Viewer) is denied on download.

    create_default_groups() is called explicitly so the DB-backed groups reflect
    the current backends.py definition even though pytest runs with --reuse-db.
    """
    create_default_groups()
    team = TeamFactory.create()
    user = UserFactory.create()
    add_user_to_team(team, user, groups=[group_name])
    file = FileFactory.create(team=team)

    client.force_login(user)
    url = reverse("files:base", args=[team.slug, file.id])
    response = client.get(url)

    # 200 means FileView served the file; a missing files.view_file would 302-redirect to login.
    assert response.status_code == 200
