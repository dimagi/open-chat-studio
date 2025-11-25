from django.conf import settings
from drf_spectacular.authentication import TokenScheme
from drf_spectacular.extensions import OpenApiAuthenticationExtension


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


class OAuth2TeamsScheme(OpenApiAuthenticationExtension):
    target_class = "apps.oauth.permissions.OAuth2AccessTokenAuthentication"
    name = "OAuth2"
    match_subclasses = True

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
