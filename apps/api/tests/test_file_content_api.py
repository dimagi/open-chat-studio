"""Authorization tests for the file-download endpoint.

Team scoping alone is not enough: downloading file content requires the ``files.view_file`` model
permission, the same gate the web view (``apps.files.views.FileView``) enforces.
"""

import pytest
from django.urls import reverse

from apps.teams.backends import CHAT_VIEWER_GROUP, EVENT_ADMIN_GROUP, add_user_to_team
from apps.utils.factories.files import FileFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient

FILE_CONTENT = b"participant attachment"


@pytest.fixture()
def team(db):
    return TeamFactory.create()


@pytest.fixture()
def file(team):
    return FileFactory.create(team=team, file__filename="attachment.txt", file__data=FILE_CONTENT)


def _download(client, file):
    return client.get(reverse("api:file-content", kwargs={"pk": file.id}))


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_member_with_view_file_permission_can_download(auth_method, team, file):
    user = UserFactory.create()
    add_user_to_team(team, user, [CHAT_VIEWER_GROUP])
    client = ApiTestClient(user, team, auth_method=auth_method)

    response = _download(client, file)

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == FILE_CONTENT


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_member_without_view_file_permission_is_forbidden(auth_method, team, file):
    """Event Admin grants no files permissions, so it cannot download team files by pk."""
    user = UserFactory.create()
    add_user_to_team(team, user, [EVENT_ADMIN_GROUP])
    client = ApiTestClient(user, team, auth_method=auth_method)

    response = _download(client, file)

    assert response.status_code == 403


@pytest.mark.django_db()
def test_machine_token_with_files_scope_can_download(team, file):
    """A client-credentials token has no user or membership; the OAuth scope is its authorization."""
    client = ApiTestClient(UserFactory.create(), team, auth_method="oauth_client_credentials", scopes=["files:read"])

    response = _download(client, file)

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == FILE_CONTENT


@pytest.mark.django_db()
def test_machine_token_without_files_scope_is_forbidden(team, file):
    client = ApiTestClient(UserFactory.create(), team, auth_method="oauth_client_credentials", scopes=["sessions:read"])

    response = _download(client, file)

    assert response.status_code == 403
