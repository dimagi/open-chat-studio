"""Builds the OpenAPI response-body schemas for the per-resource team-export sync endpoints. Each
resource is documented with the shape of the same dynamic serializer the endpoint actually serves,
so the docs can't drift from the payload. The per-resource view factory that attaches these schemas
lives with the view in ``views.py``."""

from drf_spectacular.utils import OpenApiResponse
from rest_framework import serializers

from apps.teams.export.manifest import SECRET_REGISTRY, ManifestEntry, entry_model

from .serializers import build_resource_serializer


class SyncErrorDetail(serializers.Serializer):
    detail = serializers.CharField()


def build_docs_item_serializer(entry: ManifestEntry) -> type[serializers.Serializer]:
    """The runtime row serializer, with secret fields redeclared as the sealed base64 string they
    actually serialize to rather than their raw model type."""
    model = entry_model(entry.model)
    base = build_resource_serializer(model)
    secret_fields = SECRET_REGISTRY.get(model._meta.label_lower, [])
    if not secret_fields:
        return base
    overrides = {
        field: serializers.CharField(help_text="Sealed under the team's public key (base64, RSA-OAEP).")
        for field in secret_fields
    }
    return type(f"{model.__name__}ExportItem", (base,), overrides)


def build_resource_response_serializer(entry: ManifestEntry) -> type[serializers.Serializer]:
    """The paginated envelope the resource endpoint returns: cursor, has_more, and the page of rows."""
    model = entry_model(entry.model)
    item = build_docs_item_serializer(entry)
    return type(
        f"{model.__name__}ExportPage",
        (serializers.Serializer,),
        {
            "cursor": serializers.CharField(
                allow_null=True, help_text="Cursor for the next page; null once the resource is exhausted."
            ),
            "has_more": serializers.BooleanField(help_text="True while more rows remain beyond this page."),
            "results": item(many=True),
        },
    )


def resource_responses(entry: ManifestEntry) -> dict[int, type[serializers.Serializer] | OpenApiResponse]:
    """Per-status responses for one resource: the 200 envelope, and a 409 (secret resources only)
    when the team has no public key to seal against. No 404: routing prevents unknown resources from
    reaching the view."""
    responses: dict[int, type[serializers.Serializer] | OpenApiResponse] = {
        200: build_resource_response_serializer(entry),
    }
    if entry.secret:
        responses[409] = OpenApiResponse(
            response=SyncErrorDetail,
            description="Team has no registered public key; secret data cannot be sealed.",
        )
    return responses
