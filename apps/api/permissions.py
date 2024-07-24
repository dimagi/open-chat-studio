from django.conf import settings
from django.utils.translation import gettext as _
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import DjangoModelPermissions
from rest_framework_api_key.permissions import KeyParser

from apps.teams.utils import set_current_team

from .helpers import get_team_membership_for_request
from .models import UserAPIKey


class BaseKeyAuthentication(BaseAuthentication):
    def get_key(self, request):
        raise NotImplementedError

    def authenticate(self, request):
        key = self.get_key(request)
        if not key:
            return None

        try:
            token = UserAPIKey.objects.get_from_key(key)
        except UserAPIKey.DoesNotExist:
            raise exceptions.AuthenticationFailed(_("Invalid token."))

        if not token.user.is_active:
            raise exceptions.AuthenticationFailed(_("User inactive or deleted."))

        user = token.user
        request.user = user
        request.team = token.team
        request.team_membership = get_team_membership_for_request(request)
        if not request.team_membership:
            raise exceptions.AuthenticationFailed()

        # this is unset by the request_finished signal
        set_current_team(token.team)
        return user, token


class ApiKeyAuthentication(BaseKeyAuthentication):
    def get_key(self, request):
        return KeyParser().get_from_header(request, settings.API_KEY_CUSTOM_HEADER)


class BearerTokenAuthentication(BaseKeyAuthentication):
    keyword = "Bearer"

    def get_key(self, request):
        return ConfigurableKeyParser(keyword=self.keyword).get_from_authorization(request)


class ConfigurableKeyParser(KeyParser):
    def __init__(self, keyword: str):
        self.keyword = keyword


class DjangoModelPermissionsWithView(DjangoModelPermissions):
    perms_map = {
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": [],
        "HEAD": [],
        "POST": ["%(app_label)s.add_%(model_name)s"],
        "PUT": ["%(app_label)s.change_%(model_name)s"],
        "PATCH": ["%(app_label)s.change_%(model_name)s"],
        "DELETE": ["%(app_label)s.delete_%(model_name)s"],
    }
