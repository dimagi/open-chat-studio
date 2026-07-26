import copy

from django.conf import settings
from drf_spectacular.authentication import TokenScheme
from drf_spectacular.extensions import OpenApiAuthenticationExtension, OpenApiSerializerFieldExtension
from rest_framework.permissions import SAFE_METHODS

from apps.oauth.permissions import TokenHasOAuthResourceScope, TokenHasOAuthScope

# Placeholder hosts baked into the schema: DRF hardcodes ``api.example.org`` in the cursor
# pagination ``next``/``previous`` examples, and our serializer/operation examples use
# ``example.com``. ``set_example_urls`` rewrites both to the serving deployment's host.
_PLACEHOLDER_HOSTS = ("https://example.com", "http://example.com")
_PAGINATION_PLACEHOLDER = "http://api.example.org/accounts/"

# Host used when no request is available (the offline ``spectacular`` management command that builds
# the committed ``api-schemas/*.yml``). Keeps those artifacts deterministic and deployment-neutral;
# live-served schemas use the real request host instead.
_FALLBACK_BASE_URL = "https://example.com"


def exclude_legacy_participants_path(endpoints):
    """Drop the trailing-slash ``/api/participants/`` alias from the schema.

    ``/api/participants`` (no slash) is canonical — it's the only one referenced via ``reverse()``;
    the slash variant is a backwards-compat alias kept only so existing callers don't break. Both
    routes share a view, so documenting both produces duplicate operationIds (``list_participants``
    / ``list_participants_2`` etc.). This is a preprocessing hook (signature: ``endpoints`` ->
    filtered ``endpoints``).
    """
    return [endpoint for endpoint in endpoints if endpoint[0] != "/api/participants/"]


EXPORT_DESCRIPTION = (
    "Read-only, team-scoped endpoints for the Open Chat Studio data sync/export. "
    "**Unversioned and intended only for OCS export** — it carries no "
    "backwards-compatibility guarantee and may change without notice."
)


def set_export_description(result, generator, **kwargs):
    """Give the export schema its own ``info.description``.

    Lives here rather than in the served view's ``custom_settings`` so the committed
    ``api-schemas/export.yml`` matches what ``/api/export/schema/`` serves: that file is built by the
    ``spectacular`` management command, which reads the global ``SPECTACULAR_SETTINGS`` and ignores
    per-view ``custom_settings``. A postprocessing hook runs for both, keyed on the api version.
    """
    if generator.api_version == "export":
        result["info"]["description"] = EXPORT_DESCRIPTION
    return result


def prune_unused_tags(result, **kwargs):
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


def _deployment_root(request) -> str:
    """Root URL (``scheme://host``, no trailing slash) of the deployment serving this schema.

    Uses the incoming request host when the schema is served live — so the ReDoc docs pages, which
    fetch the live schema, show URLs for the actual deployment. Falls back to a fixed placeholder for
    the offline ``spectacular`` management command, which runs without a request.
    """
    if request is not None:
        return request.build_absolute_uri("/").rstrip("/")
    return _FALLBACK_BASE_URL


def _swap_host(value: str, base: str) -> str:
    """Rewrite a placeholder-host example URL to point at ``base``; leave other strings untouched."""
    if value.startswith(_PAGINATION_PLACEHOLDER):
        # DRF's ``/accounts/`` path is a generic placeholder, and it appends a stray ``"`` to the
        # ``next`` cursor example — drop both.
        return value.replace(_PAGINATION_PLACEHOLDER, f"{base}/api/").rstrip('"')
    for host in _PLACEHOLDER_HOSTS:
        if value.startswith(host):
            return base + value[len(host) :]
    return value


def _rewrite_example_urls(node, base: str) -> None:
    """Recursively rewrite placeholder-host URLs in every string value of ``node`` in place."""
    items = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
    for key, value in items:
        if isinstance(value, str):
            node[key] = _swap_host(value, base)
        elif isinstance(value, dict | list):
            _rewrite_example_urls(value, base)


class ApiUrlFieldExtension(OpenApiSerializerFieldExtension):
    """Emit the ``example`` carried by ``apps.api.serializers.ApiUrlField`` into the schema."""

    target_class = "apps.api.serializers.ApiUrlField"

    def map_serializer_field(self, auto_schema, direction):
        return {"type": "string", "format": "uri", "example": self.target.openapi_example}


def set_example_urls(result, request=None, **kwargs):
    """Point example URLs at the serving deployment instead of the ``example.com``/``example.org``
    placeholders baked into the schema. A postprocessing hook (signature: ``result`` -> ``result``).

    Deep-copies first: some example values are module-level ``OpenApiExample`` dicts that
    drf-spectacular embeds by reference and reuses across generations, so an in-place rewrite would
    leak one request's host into every later schema build."""
    result = copy.deepcopy(result)
    _rewrite_example_urls(result, _deployment_root(request))
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
                    # Any valid OAuth token is accepted, no specific scope. Return the scheme with an
                    # empty scope list — NOT ``{}``, which OpenAPI reads as "no auth required".
                    return {self.name: []}

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
