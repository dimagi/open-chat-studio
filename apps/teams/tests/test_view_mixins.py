import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404, HttpResponse
from django.test import RequestFactory, TestCase
from django.views import View

from apps.conftest import unset_current_team
from apps.teams.backends import add_user_to_team, make_user_team_owner
from apps.teams.middleware import TeamsMiddleware
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.teams.models import Team
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
        make_user_team_owner(cls.sox, cls.sox_admin)
        cls.sox_member = CustomUser.objects.create(username="papi@redsox.com")
        add_user_to_team(cls.sox, cls.sox_member)

        cls.yanks_admin = CustomUser.objects.create(username="joe.torre@yankees.com")
        make_user_team_owner(cls.yanks, cls.yanks_admin)
        cls.yanks_member = CustomUser.objects.create(username="derek.jeter@yankees.com")
        add_user_to_team(cls.yanks, cls.yanks_member)

    def _get_request(self, user=None):
        request = self.factory.get("/team/")  # the url here is ignored
        request.user = user or AnonymousUser()
        request.session = {}  # ty: ignore[invalid-assignment]
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
        assert response.status_code == 200
        assert f"Go {team_slug}" == response.content.decode("utf-8")
        unset_current_team()

    def assertRedirectToLogin(self, view_cls, user, team_slug):
        response = self._call_view(view_cls, user, team_slug)
        assert response.status_code == 302
        assert "/login/" in response.url
        unset_current_team()

    def assertNotFound(self, view_cls, user, team_slug):
        with pytest.raises(Http404):
            self._call_view(view_cls, user, team_slug)
        unset_current_team()

    def test_anonymous_user_redirect_to_login(self):
        self.assertRedirectToLogin(MemberView, AnonymousUser(), "sox")
        self.assertRedirectToLogin(MemberView, AnonymousUser(), "yanks")

    def test_member_view_logged_in(self):
        for user in [self.sox_member, self.sox_admin]:
            self.assertSuccessfulRequest(MemberView, user, "sox")
            self.assertNotFound(MemberView, user, "yanks")
        for user in [self.yanks_member, self.yanks_admin]:
            self.assertSuccessfulRequest(MemberView, user, "yanks")
            self.assertNotFound(MemberView, user, "sox")
