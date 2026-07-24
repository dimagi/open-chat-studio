from typing import cast

from django.http import HttpRequest
from rest_framework_api_key.permissions import KeyParser

from apps.api.models import UserAPIKey
from apps.users.models import CustomUser


def get_user_from_request(request: HttpRequest) -> CustomUser | None:
    if request is None:
        return None
    if request.user.is_anonymous:
        # Anonymous requests may still carry a UserAPIKey (from which we can derive the user), but a
        # client-credentials OAuth token has neither a user nor an API key, so there is no user.
        if not _get_api_key(request):
            return None
        user_api_key = _get_api_key_object(request, UserAPIKey)
        return user_api_key.user
    else:
        return cast(CustomUser, request.user)


def get_team_from_request(request: HttpRequest) -> CustomUser | None:
    if request is None:
        return None
    user_api_key = _get_api_key_object(request, UserAPIKey)
    return user_api_key.team


def _get_api_key_object(request, model_class):
    return model_class.objects.get_from_key(_get_api_key(request))


def _get_api_key(request):
    # inspired by / copied from BaseHasAPIKey.get_key()
    # loosely based on this issue: https://github.com/florimondmanca/djangorestframework-api-key/issues/98
    return KeyParser().get(request)
