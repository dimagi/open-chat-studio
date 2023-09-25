from django.test import Client, TestCase
from django.urls import reverse

from apps.teams import roles
from apps.teams.exceptions import TeamPermissionError
from apps.teams.models import Membership, Team
from apps.teams.roles import ROLE_ADMIN, ROLE_MEMBER
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

        self.team.members.add(self.admin, through_defaults={"role": ROLE_ADMIN})
        self.team.members.add(self.admin2, through_defaults={"role": ROLE_ADMIN})
        self.team.members.add(self.member, through_defaults={"role": ROLE_MEMBER})
        self.team.members.add(self.member2, through_defaults={"role": ROLE_MEMBER})

        self.admin_membership = Membership.objects.get(user=self.admin, team=self.team)
        self.admin_membership2 = Membership.objects.get(user=self.admin2, team=self.team)
        self.normal_membership = Membership.objects.get(user=self.member, team=self.team)
        self.normal_membership2 = Membership.objects.get(user=self.member2, team=self.team)

    def _get_membership_url(self, membership):
        return reverse("single_team:team_membership_details", args=[self.team.slug, membership.pk])

    def _get_remove_membership_url(self, membership):
        return reverse("single_team:remove_team_membership", args=[self.team.slug, membership.pk])

    def _change_role(self, client, membership, role):
        return client.post(self._get_membership_url(membership), {"role": role})

    def _remove_member(self, client, membership):
        return client.post(self._get_remove_membership_url(membership))

    # happy path tests
    def test_admins_can_view_all_members(self):
        c = Client()
        c.force_login(self.admin)
        for membership in [self.admin_membership, self.admin_membership2, self.normal_membership]:
            response = c.get(self._get_membership_url(membership))
            self.assertEqual(200, response.status_code)
            self.assertTrue(membership.user.get_display_name() in response.content.decode("utf-8"))

    def test_admins_can_change_others_roles(self):
        c = Client()
        c.force_login(self.admin)
        # change member to admin
        response = self._change_role(c, self.normal_membership, roles.ROLE_ADMIN)
        self.assertEqual(200, response.status_code)
        # confirm updated
        self.normal_membership.refresh_from_db()
        self.assertEqual(roles.ROLE_ADMIN, self.normal_membership.role)
        # change back
        response = self._change_role(c, self.normal_membership, roles.ROLE_MEMBER)
        self.assertEqual(200, response.status_code)
        # confirm updated
        self.normal_membership.refresh_from_db()
        self.assertEqual(roles.ROLE_MEMBER, self.normal_membership.role)

    def test_admins_can_remove_members(self):
        c = Client()
        c.force_login(self.admin)
        self._remove_member(c, self.normal_membership)
        # confirm member removed
        self.assertFalse(Membership.objects.filter(pk=self.normal_membership.pk).exists())

    def test_admins_can_remove_admins(self):
        c = Client()
        c.force_login(self.admin)
        self._remove_member(c, self.admin_membership2)
        # confirm member removed
        self.assertFalse(Membership.objects.filter(pk=self.admin_membership2.pk).exists())

    def test_members_can_view_own_membership(self):
        c = Client()
        c.force_login(self.member)
        response = c.get(self._get_membership_url(self.normal_membership))
        self.assertEqual(200, response.status_code)
        self.assertTrue(self.member.get_display_name() in response.content.decode("utf-8"))

    def test_members_can_leave_team(self):
        c = Client()
        c.force_login(self.member)
        self._remove_member(c, self.normal_membership)
        # confirm member removed
        self.assertFalse(Membership.objects.filter(pk=self.normal_membership.pk).exists())

    # edge case / permission tests
    def test_members_cant_view_other_members(self):
        c = Client()
        c.force_login(self.member)
        for other_membership in [self.admin_membership, self.normal_membership2]:
            response = c.get(self._get_membership_url(self.admin_membership))
            # should either be a 404 or a redirect
            self.assertNotEqual(200, response.status_code)

    def test_members_cant_change_others_roles(self):
        c = Client()
        c.force_login(self.member)
        response = self._change_role(c, self.normal_membership2, roles.ROLE_ADMIN)
        self.assertNotEqual(200, response.status_code)
        # confirm not changed
        self.normal_membership2.refresh_from_db()
        self.assertEqual(roles.ROLE_MEMBER, self.normal_membership2.role)

    def test_members_cant_change_own_role(self):
        for membership in [self.normal_membership, self.admin_membership]:
            original_role = membership.role
            new_role = roles.ROLE_ADMIN if original_role == roles.ROLE_MEMBER else roles.ROLE_MEMBER
            c = Client()
            c.force_login(membership.user)
            # trying to change fails hard
            with self.assertRaises(TeamPermissionError):
                self._change_role(c, membership, new_role)

            # confirm unchanged
            membership.refresh_from_db()
            self.assertEqual(original_role, membership.role)

    def test_only_admin_cant_leave(self):
        c = Client()
        c.force_login(self.admin)

        # demote other admin
        self._change_role(c, self.admin_membership2, roles.ROLE_MEMBER)

        # confirm it doesn't work
        response = self._remove_member(c, self.admin_membership)
        self.assertNotEqual(200, response.status_code)
        self.assertTrue(Membership.objects.filter(pk=self.admin_membership.pk).exists())
