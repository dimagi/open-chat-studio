from django.conf import settings
from drf_spectacular.authentication import TokenScheme
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.permissions import SAFE_METHODS

from apps.oauth.permissions import TokenHasOAuthResourceScope, TokenHasOAuthScope


def exclude_legacy_participants_path(endpoints):
    """Drop the trailing-slash ``/api/participants/`` alias from the schema.

    ``/api/participants`` (no slash) is canonical — it's the only one referenced via ``reverse()``;
    the slash variant is a backwards-compat alias kept only so existing callers don't break. Both
    routes share a view, so documenting both produces duplicate operationIds (``list_participants``
    / ``list_participants_2`` etc.). This is a preprocessing hook (signature: ``endpoints`` ->
    filtered ``endpoints``).
    """
    return [endpoint for endpoint in endpoints if endpoint[0] != "/api/participants/"]


def prune_unused_tags(result, generator, request, public, **kwargs):
    """Drop top-level tag definitions not referenced by any operation.

    ``SPECTACULAR_SETTINGS["TAGS"]`` is a single global list applied to every schema, so a
    per-version schema would otherwise advertise tags from versions it doesn't include (e.g. v2
    listing v1's "Experiments"/"Participants"). Keep only the tags its operations actually use.
    """
    used = {
        tag
        for path in result.get("paths", {}).values()
        for op in path.values()
        if isinstance(op, dict)
        for tag in op.get("tags", [])
    }
    if "tags" in result:
        result["tags"] = [tag for tag in result["tags"] if tag["name"] in used]
    return result


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
                "configuration and authenticates embedded widget requests."
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

                formatted_scopes = [f"{scope}:{scope_type}" for scope in required_scopes]
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
