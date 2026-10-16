from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse

from apps.teams.decorators import login_and_team_required


@login_and_team_required
def _view(request, team_slug):
    return HttpResponse()


def test_login_redirect_keeps_the_whole_path_in_next():
    path = "/a/acme/settings/?section=moderation&page=2"
    request = RequestFactory().get(path)
    request.user = AnonymousUser()

    response = _view(request, team_slug="acme")

    redirect = urlsplit(response.url)
    assert redirect.path == reverse(settings.LOGIN_URL)
    assert parse_qs(redirect.query) == {"next": [path]}
