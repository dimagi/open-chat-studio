"""The app shell (`web/app/app_base.html`) capped every authenticated page at
1536px via `xl:container`, wasting real space on wide displays (#4408). Guards
against reintroducing that cap on the shared shell 58 templates extend.
"""

import pytest
from django.urls import reverse


@pytest.mark.django_db()
def test_authenticated_page_has_no_shell_width_cap(client, team_with_users):
    team = team_with_users
    user = team.members.first()
    client.force_login(user)

    response = client.get(reverse("dashboard:index", kwargs={"team_slug": team.slug}))

    assert response.status_code == 200
    assert "xl:container" not in response.content.decode()
