import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404, HttpResponse
from django.test import RequestFactory, TestCase
from django.views import View

from apps.teams.middleware import TeamsMiddleware
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.models import Membership, Team
from apps.teams.roles import ROLE_ADMIN, ROLE_MEMBER
from apps.users.models import CustomUser


class BaseView(View):
    def get(self, *args, **kwargs) -> HttpResponse:
        return HttpResponse(f"Go {self.request.team.slug}")


class MemberView(LoginAndTeamRequiredMixin, BaseView):
    pass


class TeamMixinTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.factory = RequestFactory()

        cls.sox = Team.objects.create(name="Red Sox", slug="sox")
        cls.yanks = Team.objects.create(name="Yankees", slug="yanks")

        cls.sox_admin = CustomUser.objects.create(username="tito@redsox.com")
        cls.sox_member = CustomUser.objects.create(username="papi@redsox.com")
        Membership.objects.create(user=cls.sox_admin, role=ROLE_ADMIN, team=cls.sox)
        Membership.objects.create(user=cls.sox_member, role=ROLE_MEMBER, team=cls.sox)

        cls.yanks_admin = CustomUser.objects.create(username="joe.torre@yankees.com")
        cls.yanks_member = CustomUser.objects.create(username="derek.jeter@yankees.com")
        Membership.objects.create(user=cls.yanks_admin, role=ROLE_ADMIN, team=cls.yanks)
        Membership.objects.create(user=cls.yanks_member, role=ROLE_MEMBER, team=cls.yanks)

    def _get_request(self, user=None):
        request = self.factory.get("/team/")  # the url here is ignored
        request.user = user or AnonymousUser()
        request.session = {}
        return request

    def _call_view(self, view_cls, user, team_slug):
        request = self._get_request(user=user)
        view_kwargs = {"team_slug": team_slug}

        def get_response(req):
            return view_cls.as_view()(req, **view_kwargs)

        middleware = TeamsMiddleware(get_response=get_response)
        middleware.process_view(request, None, None, view_kwargs)
        return middleware(request)

    def assertSuccessfulRequest(self, view_cls, user, team_slug):
        response = self._call_view(view_cls, user, team_slug)
        assert 200 == response.status_code
        assert f"Go {team_slug}" == response.content.decode("utf-8")

    def assertRedirectToLogin(self, view_cls, user, team_slug):
        response = self._call_view(view_cls, user, team_slug)
        assert 302 == response.status_code
        assert "/login/" in response.url

    def assertNotFound(self, view_cls, user, team_slug):
        with pytest.raises(Http404):
            self._call_view(view_cls, user, team_slug)

    def test_anonymous_user_redirect_to_login(self):
        self.assertRedirectToLogin(MemberView, AnonymousUser(), "sox")
        self.assertRedirectToLogin(MemberView, AnonymousUser(), "yanks")

    def test_member_view_logged_in(self):
        for user in [self.sox_member, self.sox_member]:
            self.assertSuccessfulRequest(MemberView, user, "sox")
            self.assertNotFound(MemberView, user, "yanks")
        for user in [self.yanks_member, self.yanks_admin]:
            self.assertSuccessfulRequest(MemberView, user, "yanks")
            self.assertNotFound(MemberView, user, "sox")
