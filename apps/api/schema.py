from django.conf import settings
from drf_spectacular.authentication import TokenScheme
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.permissions import SAFE_METHODS

from apps.oauth.permissions import TokenHasOAuthResourceScope, TokenHasOAuthScope


class ApiScheme(OpenApiAuthenticationExtension):
    target_class = "apps.api.permissions.ApiKeyAuthentication"
    name = "apiKeyAuth"
    match_subclasses = True
    priority = -1

    def get_security_definition(self, auto_schema):
        header_name = settings.API_KEY_CUSTOM_HEADER
        if header_name.startswith("HTTP_"):
            header_name = header_name[5:]
        header_name = header_name.replace("_", "-").capitalize()

        return {
            "type": "apiKey",
            "in": "header",
            "name": header_name,
        }


class BearerScheme(TokenScheme):
    target_class = "apps.api.permissions.BearerTokenAuthentication"


class EmbeddedWidgetScheme(OpenApiAuthenticationExtension):
    target_class = "apps.api.authentication.EmbeddedWidgetAuthentication"
    name = "embedKeyAuth"
    match_subclasses = True
    priority = -1

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "X-Embed-Key",
            "description": (
                "Embedded widget authentication token. Validates the widget token for the channel "
                "configuration and authenticates embedded widget requests. Requires chatbot_id "
                "in the request body for /api/chat/start/ or from session data for subsequent requests."
            ),
        }


class OAuth2TeamsScheme(OpenApiAuthenticationExtension):
    target_class = "apps.oauth.permissions.OAuth2AccessTokenAuthentication"
    name = "OAuth2"
    match_subclasses = True

    def get_security_requirement(self, auto_schema):
        view = auto_schema.view

        # Check if view uses OAuth scope checking permissions
        for permission in view.get_permissions():
            if isinstance(permission, TokenHasOAuthResourceScope):
                # Get the required scopes from the view
                required_scopes = getattr(view, "required_scopes", [])
                if not required_scopes:
                    return {}

                # Format scopes with :read or :write suffix like TokenHasResourceScope does
                method = auto_schema.method.upper()
                if method in SAFE_METHODS:
                    scope_type = "read"
                else:
                    scope_type = "write"

                formatted_scopes = [
                    f"{scope}:{scope_type}" for scope in required_scopes
                ]
                return {self.name: formatted_scopes}
            elif isinstance(permission, TokenHasOAuthScope):
                # Get the required scopes from the view (TokenHasScope.get_scopes)
                required_scopes = getattr(view, "required_scopes", [])
                if required_scopes:
                    return {self.name: required_scopes}

        return {}

    def get_security_definition(self, auto_schema):
        return {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": "/o/authorize/",
                    "tokenUrl": "/o/token/",
                    "scopes": settings.OAUTH2_PROVIDER.get("SCOPES", {}),
                }
            },
        }
