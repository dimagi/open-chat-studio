from oauth2_provider.oauth2_validators import OAuth2Validator

from apps.teams.utils import get_current_team

from .models import OAuth2Grant


class APIScopedValidator(OAuth2Validator):
    """
    OAuth2 validator that associates authorization codes and access tokens with teams.

    The flow is as follows:
    1. When the user grants authorization, the selected team is set in the thread's context. This happens in the
        TeamScopedAuthorizationView.
    2. When creating the authorization code, we read the team from the context and associate it with the code (Grant).
    3. When validating the code (from a new request context), we load the team from the Grant onto the request
    4. When creating the access token, we read the team from the request and associate it with the token.
    """

    def _create_authorization_code(self, request, code, expires=None):
        grant = super()._create_authorization_code(request, code, expires)
        team = get_current_team()
        grant.team = team
        grant.save()
        return grant

    def validate_code(self, client_id, code, client, request, *args, **kwargs):
        is_valid = super().validate_code(client_id, code, client, request, *args, **kwargs)
        if not is_valid:
            return False

        # Load the team onto the request so that it can be used later to update the access token
        grant = OAuth2Grant.objects.get(code=code, application=client)
        request.team = grant.team
        return True

    def _create_access_token(self, expires, request, token, source_refresh_token=None):
        access_token = super()._create_access_token(expires, request, token, source_refresh_token)
        access_token.team = request.team
        access_token.save()
        return access_token
