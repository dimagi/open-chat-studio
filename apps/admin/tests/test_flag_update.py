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
            {"everyone": "true", "teams": [team.id]},
        )

        assert response.status_code == 200
        flag.refresh_from_db()
        assert flag.everyone is True
        assert list(flag.teams.all()) == [team]

    @pytest.mark.parametrize(
        ("posted", "expected"),
        [
            pytest.param({"everyone": "true"}, True, id="on-for-everyone"),
            pytest.param({"everyone": "false"}, False, id="off-for-everyone"),
            pytest.param({"everyone": "unknown"}, None, id="use-teams"),
            pytest.param({}, None, id="absent-key-means-use-teams"),
        ],
    )
    def test_everyone_is_written_as_a_tri_state(self, superuser_client, posted, expected):
        """`everyone` is on for everyone, off for everyone, or defer to the team list;
        an absent key must mean "use teams", not silently coerce to a hard off."""
        flag = Flag.objects.create(name="flag_update_probe", everyone=None if expected is True else True)

        response = superuser_client.post(reverse("ocs_admin:update_flag", args=[flag.name]), posted)

        assert response.status_code == 200
        flag.refresh_from_db()
        assert flag.everyone is expected

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
        for tri_state_option in ('value="true"', 'value="false"', 'value="unknown"'):
            assert tri_state_option in content, "the everyone control must offer all three states"
        for dropped_control in (
            "formData.testing",
            "formData.superusers",
            "formData.rollout",
            "formData.percent",
            "users-select",
        ):
            assert dropped_control not in content

    @pytest.mark.parametrize(
        ("everyone", "on_badge", "off_badge"),
        [
            pytest.param(True, True, False, id="globally-on"),
            pytest.param(False, False, True, id="globally-off"),
            pytest.param(None, False, False, id="use-teams-has-no-global-badge"),
        ],
    )
    def test_list_page_badges_reflect_the_tri_state(self, superuser_client, everyone, on_badge, off_badge):
        """A hard off is as much a global decision as a rollout, so it gets its own badge;
        only the deferred state renders without one."""
        flag = Flag.objects.create(name="flag_tristate_badge_probe", everyone=everyone)

        response = superuser_client.get(reverse("ocs_admin:flags_home"))

        content = response.content.decode()
        list_item_start = content.index(f'id="flag-{flag.id}"')
        list_item = content[list_item_start : content.index("</li>", list_item_start)]
        assert ("Off for everyone" in list_item) is off_badge
        assert ("Everyone" in list_item.replace("Off for everyone", "")) is on_badge

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

    @pytest.mark.parametrize(
        ("field", "make_target"),
        [
            pytest.param("teams", lambda: TeamFactory.create(name="history-probe-team"), id="teams"),
            pytest.param("users", lambda: UserFactory.create(username="history-probe-user"), id="users"),
            pytest.param("groups", lambda: Group.objects.create(name="history-probe-group"), id="groups"),
        ],
    )
    def test_history_renders_names_not_ids(self, superuser_client, field, make_target):
        """The audit log is the tripwire for writes to neutralised fields, so every
        audited M2M delta renders its target by name rather than by row ID."""
        flag = Flag.objects.create(name="flag_history_probe")
        target = make_target()
        with enable_audit():
            getattr(flag, field).add(target)

        response = superuser_client.get(reverse("ocs_admin:flag_history", args=[flag.name]))

        content = response.content.decode()
        expected = target.get_display_name() if field == "users" else target.name
        assert expected in content
        assert f"{field.rstrip('s').title()} {target.id}" not in content
