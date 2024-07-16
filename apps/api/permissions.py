import typing

from django.http import HttpRequest
from django.utils.translation import gettext as _
from rest_framework import exceptions
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import DjangoModelPermissions, IsAuthenticated
from rest_framework_api_key.permissions import BaseHasAPIKey

from apps.teams.utils import set_current_team

from .helpers import get_team_from_request, get_team_membership_for_request, get_user_from_request
from .models import UserAPIKey


class BearerTokenAuthentication(TokenAuthentication):
    """Used by OpenAI API"""

    keyword = "Bearer"
    model = UserAPIKey

    def authenticate(self, request):
        user_auth_tuple = super().authenticate(request)
        if user_auth_tuple:
            user, api_key = user_auth_tuple
            request.user = user
            request.team = api_key.team
            request.team_membership = get_team_membership_for_request(request)
            if not request.team_membership:
                raise exceptions.AuthenticationFailed()

            # this is unset by the request_finished signal
            set_current_team(api_key.team)
        return user_auth_tuple

    def authenticate_credentials(self, key):
        model = self.get_model()
        try:
            token = model.objects.get_from_key(key)
        except model.DoesNotExist:
            raise exceptions.AuthenticationFailed(_("Invalid token."))

        if not token.user.is_active:
            raise exceptions.AuthenticationFailed(_("User inactive or deleted."))

        return token.user, token


class HasUserAPIKey(BaseHasAPIKey):
    model = UserAPIKey

    def has_permission(self, request: HttpRequest, view: typing.Any) -> bool:
        has_perm = super().has_permission(request, view)
        if has_perm:
            # if they have permission, also populate the request.user object for convenience
            request.user = get_user_from_request(request)
            request.team = get_team_from_request(request)
            request.team_membership = get_team_membership_for_request(request)
            if not request.team_membership:
                return False

            # this is unset by the request_finished signal
            set_current_team(request.team)
        return has_perm


# hybrid permission class that can check for API keys or authentication
IsAuthenticatedOrHasUserAPIKey = IsAuthenticated | HasUserAPIKey


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
