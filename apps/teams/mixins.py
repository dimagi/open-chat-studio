from django.contrib.auth.mixins import UserPassesTestMixin

from apps.teams.decorators import valid_auth_and_membership


class LoginAndTeamRequiredMixin(UserPassesTestMixin):
    """
    Verify that the current user is authenticated and a member of the team.
    """

    def test_func(self):
        return valid_auth_and_membership(self.request.user, self.request.team)
