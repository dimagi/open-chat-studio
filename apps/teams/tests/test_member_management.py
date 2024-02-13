import pytest
from django.test import Client, TestCase
from django.urls import reverse

from apps.teams.backends import (
    CHAT_VIEWER_GROUP,
    EXPERIMENT_ADMIN_GROUP,
    SUPER_ADMIN_GROUP,
    add_user_to_team,
    get_groups,
    make_user_team_owner,
)
from apps.teams.exceptions import TeamPermissionError
from apps.teams.models import Membership, Team
from apps.users.models import CustomUser


class TeamMemberManagementViewTest(TestCase):
    """
    Tests that exercise the various bits of view logic surrounding who is allowed to modify team memberships
    and remove people / leave teams.
    """

    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(name="Red Sox", slug="sox")
        self.admin = CustomUser.objects.create(username="tito@redsox.com")
        self.admin2 = CustomUser.objects.create(username="alex@redsox.com")
        self.member = CustomUser.objects.create(username="papi@redsox.com")
        self.member2 = CustomUser.objects.create(username="manny@redsox.com")

        self.admin_membership = make_user_team_owner(self.team, self.admin)
        self.admin_membership2 = make_user_team_owner(self.team, self.admin2)
        self.normal_membership = add_user_to_team(self.team, self.member)
        self.normal_membership2 = add_user_to_team(self.team, self.member2)

        self.groups = get_groups()
        self.admin_groups = [self.groups[SUPER_ADMIN_GROUP]]
        self.admin_group_ids = {g.id for g in self.admin_groups}
        self.member_groups = [self.groups[EXPERIMENT_ADMIN_GROUP], self.groups[CHAT_VIEWER_GROUP]]
        self.member_group_ids = {g.id for g in self.member_groups}

    def _get_membership_url(self, membership):
        return reverse("single_team:team_membership_details", args=[self.team.slug, membership.pk])

    def _get_remove_membership_url(self, membership):
        return reverse("single_team:remove_team_membership", args=[self.team.slug, membership.pk])

    def _change_role(self, client, membership, groups):
        return client.post(self._get_membership_url(membership), {"groups": [g.id for g in groups]})

    def _remove_member(self, client, membership):
        return client.post(self._get_remove_membership_url(membership))

    # happy path tests
    def test_admins_can_view_all_members(self):
        c = Client()
        c.force_login(self.admin)
        for membership in [self.admin_membership, self.admin_membership2, self.normal_membership]:
            response = c.get(self._get_membership_url(membership))
            assert 200 == response.status_code
            assert membership.user.get_display_name() in response.content.decode("utf-8")

    def test_admins_can_change_others_roles(self):
        c = Client()
        c.force_login(self.admin)
        # change member to admin
        response = self._change_role(c, self.normal_membership, self.admin_groups)
        assert 200 == response.status_code
        # confirm updated
        self._check_groups(self.normal_membership, self.admin_group_ids)

        # change back
        response = self._change_role(c, self.normal_membership, self.member_groups)
        assert 200 == response.status_code
        # confirm updated
        self._check_groups(self.normal_membership, self.member_group_ids)

    def test_admins_can_remove_members(self):
        c = Client()
        c.force_login(self.admin)
        self._remove_member(c, self.normal_membership)
        # confirm member removed
        assert not Membership.objects.filter(pk=self.normal_membership.pk).exists()

    def test_admins_can_remove_admins(self):
        c = Client()
        c.force_login(self.admin)
        self._remove_member(c, self.admin_membership2)
        # confirm member removed
        assert not Membership.objects.filter(pk=self.admin_membership2.pk).exists()

    def test_members_can_view_own_membership(self):
        c = Client()
        c.force_login(self.member)
        response = c.get(self._get_membership_url(self.normal_membership))
        assert 200 == response.status_code
        assert self.member.get_display_name() in response.content.decode("utf-8")

    def test_members_can_leave_team(self):
        c = Client()
        c.force_login(self.member)
        self._remove_member(c, self.normal_membership)
        # confirm member removed
        assert not Membership.objects.filter(pk=self.normal_membership.pk).exists()

    # edge case / permission tests
    def test_members_cant_view_other_members(self):
        c = Client()
        c.force_login(self.member)
        for other_membership in [self.admin_membership, self.normal_membership2]:
            response = c.get(self._get_membership_url(self.admin_membership))
            # should either be a 404 or a redirect
            assert 200 != response.status_code

    def test_members_cant_change_others_roles(self):
        c = Client()
        c.force_login(self.member)
        response = self._change_role(c, self.normal_membership2, self.admin_groups)
        assert 200 != response.status_code
        # confirm not changed
        self._check_groups(self.normal_membership2, set())

    def test_members_cant_change_own_role(self):
        for membership in [self.normal_membership, self.admin_membership]:
            original_role = membership.role
            c = Client()
            c.force_login(membership.user)
            # trying to change fails hard
            with pytest.raises(TeamPermissionError):
                self._change_role(c, membership, self.admin_groups)

            # confirm unchanged
            membership.refresh_from_db()
            assert original_role == membership.role

    def test_only_admin_cant_leave(self):
        c = Client()
        c.force_login(self.admin)

        # demote other admin
        self._change_role(c, self.admin_membership2, self.member_groups)

        # confirm it doesn't work
        response = self._remove_member(c, self.admin_membership)
        assert 200 != response.status_code
        assert Membership.objects.filter(pk=self.admin_membership.pk).exists()

    def _check_groups(self, membership, expected_group_ids):
        # do full reload from the DB to clearn M2M cache
        membership = Membership.objects.get(id=membership.id)
        assert expected_group_ids == set(membership.groups.values_list("id", flat=True))
