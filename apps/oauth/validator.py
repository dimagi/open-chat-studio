from oauth2_provider.oauth2_validators import OAuth2Validator

from apps.teams.utils import get_current_team, get_slug_for_team
from apps.users.helpers import user_has_confirmed_email_address

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

    oidc_claim_scope = OAuth2Validator.oidc_claim_scope
    oidc_claim_scope.update({"is_active": "openid", "team": "openid", "email_verified": "openid"})

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
        """This will be hit whenever an access token is created, including during refresh."""
        access_token = super()._create_access_token(expires, request, token, source_refresh_token)
        # Authorization-code: team comes from the request (set in validate_code) or the source refresh
        # token on refresh. Client-credentials has neither, so fall back to the team pinned on the
        # Application (request.client).
        team = getattr(request, "team", None)
        if not team and source_refresh_token:
            team = source_refresh_token.team
        if not team:
            team = getattr(request.client, "team", None)
        access_token.team = team
        access_token.save()
        return access_token

    def _create_refresh_token(self, request, refresh_token_code, access_token, previous_refresh_token):
        """This will be hit whenever an access token is created, including during refresh."""
        refresh_token = super()._create_refresh_token(request, refresh_token_code, access_token, previous_refresh_token)
        refresh_token.team = getattr(request, "team", None) or previous_refresh_token.team
        refresh_token.save()
        return refresh_token

    def get_additional_claims(self, request):
        # `sub` is the user's email, so we also assert whether that email has been verified. This lets
        # downstream consumers that federate against OCS decide whether to trust the asserted email
        # (e.g. for cross-provider account linking) instead of assuming it has been verified.
        # Client-credentials tokens have no user, so the user-derived claims are omitted.
        user = getattr(request, "user", None)
        claims = {}
        if user and user.is_authenticated:
            claims = {
                "sub": user.email,
                "name": user.get_full_name(),
                "is_active": user.is_active,
                "email_verified": user_has_confirmed_email_address(user, user.email),
            }
        if team := getattr(request, "team", None):
            claims["team"] = team.slug
        else:
            token = getattr(request, "access_token", None)
            if token and getattr(token, "team_id", None):
                claims["team"] = get_slug_for_team(token.team_id)
        return claims
