"""A flag decision is `everyone` or `teams`. The other waffle inputs (`superusers`,
`testing`, `rollout`, `percent`, `users`) only apply on request paths, which team-scoped
flag checks don't reliably have, so the flag admin neither renders nor writes them.
"""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse
from field_audit import enable_audit

from apps.admin.forms import FlagUpdateForm
from apps.teams.models import Flag
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


def test_form_exposes_only_everyone_and_teams():
    """The form is the write surface for a flag, so it carries no request-only inputs."""
    assert set(FlagUpdateForm().fields) == {"everyone", "teams"}


@pytest.mark.django_db()
class TestUpdateFlag:
    def test_sets_everyone_and_teams(self, superuser_client):
        """The two supported inputs are written to the flag."""
        flag = Flag.objects.create(name="flag_update_probe")
        team = TeamFactory.create()

        response = superuser_client.post(
            reverse("ocs_admin:update_flag", args=[flag.name]),
            {"everyone": "on", "teams": [team.id]},
        )

        assert response.status_code == 200
        flag.refresh_from_db()
        assert flag.everyone is True
        assert list(flag.teams.all()) == [team]

    def test_request_only_inputs_are_ignored(self, superuser_client):
        """Posting the dropped fields leaves them untouched rather than writing them back."""
        flag = Flag.objects.create(name="flag_update_probe")
        user = UserFactory.create()

        response = superuser_client.post(
            reverse("ocs_admin:update_flag", args=[flag.name]),
            {"testing": "on", "rollout": "on", "percent": "50", "users": [user.id]},
        )

        assert response.status_code == 200
        flag.refresh_from_db()
        assert flag.superusers is True, "waffle's default was overwritten by an absent checkbox"
        assert flag.testing is False
        assert flag.rollout is False
        assert flag.percent is None
        assert not flag.users.exists()


@pytest.mark.django_db()
class TestFlagPages:
    def test_detail_page_offers_only_everyone_and_teams_controls(self, superuser_client):
        """The Alpine form state mirrors the form: no controls for the dropped fields."""
        flag = Flag.objects.create(name="flag_detail_probe")

        response = superuser_client.get(reverse("ocs_admin:flag_detail", args=[flag.name]))

        content = response.content.decode()
        assert "formData.everyone" in content
        assert "teams-select" in content
        for dropped_control in (
            "formData.testing",
            "formData.superusers",
            "formData.rollout",
            "formData.percent",
            "users-select",
        ):
            assert dropped_control not in content

    def test_list_page_shows_only_the_everyone_badge(self, superuser_client):
        """Request-only field values still stored on a flag no longer render as badges."""
        flag = Flag.objects.create(
            name="flag_badge_probe",
            everyone=True,
            testing=True,
            superusers=True,
            staff=True,
            authenticated=True,
            rollout=True,
            percent=25,
        )
        flag.users.add(UserFactory.create())

        response = superuser_client.get(reverse("ocs_admin:flags_home"))

        # Scope assertions to the flag's own list item so flag descriptions elsewhere
        # on the page can't collide with the badge words.
        content = response.content.decode()
        list_item_start = content.index(f'id="flag-{flag.id}"')
        list_item = content[list_item_start : content.index("</li>", list_item_start)]
        assert "Everyone" in list_item
        for dropped_badge in ("Testing", "Superusers", "Staff", "Authenticated", "Rollout"):
            assert dropped_badge not in list_item

    def test_history_renders_group_names_not_ids(self, superuser_client):
        """The audit log is the tripwire for writes to neutralised fields, so a `groups`
        delta renders the group's name, as `teams` and `users` deltas already do."""
        flag = Flag.objects.create(name="flag_history_probe")
        group = Group.objects.create(name="history-probe-group")
        with enable_audit():
            flag.groups.add(group)

        response = superuser_client.get(reverse("ocs_admin:flag_history", args=[flag.name]))

        content = response.content.decode()
        assert "history-probe-group" in content
