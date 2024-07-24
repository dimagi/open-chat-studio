from drf_spectacular.authentication import TokenScheme
from drf_spectacular.extensions import OpenApiAuthenticationExtension

from gpt_playground import settings


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
