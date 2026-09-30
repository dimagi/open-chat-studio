"""The team's content resources: source material and consent forms (#4145).

Only working versions are reachable. Published chatbots hold their own snapshot of each resource, so
an edit here reaches them only once the chatbot is published again. Deleting archives rather than
destroys.
"""

from django.db import models, transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import mixins, status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import BASE_PERMISSION_CLASSES, DjangoModelPermissionsWithView
from apps.api.v2.content.serializers import (
    ArchiveRefusedSerializer,
    ConsentFormResourceSerializer,
    ContentArchivedSerializer,
    SourceMaterialResourceSerializer,
)
from apps.api.v2.write.base import DescribesPatch
from apps.experiments.models import ConsentForm, SourceMaterial
from apps.oauth.permissions import TokenHasOAuthResourceScope


class ArchiveRefused(APIException):
    """The resource cannot be archived while it is in its current state."""

    status_code = status.HTTP_409_CONFLICT


class ContentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    """List, retrieve, create, patch and archive one team-scoped, versioned content model."""

    permission_classes = [*BASE_PERMISSION_CLASSES, DjangoModelPermissionsWithView, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]
    # No PUT: every edit is a partial update.
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    metadata_class = DescribesPatch
    model: type[models.Model]

    def get_queryset(self) -> models.QuerySet:
        # The default manager already excludes archived rows.
        queryset = self.model.objects.working_versions_queryset().filter(team=self.request.team)
        if self.request.method in ("PATCH", "DELETE"):
            # Model.save() writes every column, so two unlocked PATCHes naming different fields
            # would overwrite one another.
            queryset = queryset.select_for_update()
        return queryset

    def partial_update(self, request, *args, **kwargs) -> Response:
        with transaction.atomic():
            return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs) -> Response:
        with transaction.atomic():
            self.archive(self.get_object())
        return Response(ContentArchivedSerializer({"archived": True}).data)

    def archive(self, instance) -> None:
        """Archive ``instance``, or raise ``ArchiveRefused``."""
        raise NotImplementedError


def _content_schema(*, noun: str, plural: str, archive_refusal: str):
    """The OpenAPI descriptions shared by both content viewsets."""
    operation_prefix = noun.lower().replace(" ", "_")
    tag = plural
    id_parameter = OpenApiParameter(
        name="id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, description=f"{noun} ID"
    )
    return extend_schema_view(
        list=extend_schema(
            operation_id=f"{operation_prefix}_list",
            summary=f"List {plural}",
            description=f"List the team's {plural.lower()}. Archived ones are not listed.",
            tags=[tag],
        ),
        retrieve=extend_schema(
            operation_id=f"{operation_prefix}_retrieve",
            parameters=[id_parameter],
            summary=f"Retrieve {noun}",
            tags=[tag],
        ),
        create=extend_schema(
            operation_id=f"{operation_prefix}_create",
            summary=f"Create {noun}",
            description="A key that is not listed below is rejected rather than ignored.",
            tags=[tag],
        ),
        partial_update=extend_schema(
            operation_id=f"{operation_prefix}_update",
            parameters=[id_parameter],
            summary=f"Update {noun}",
            description=(
                "Only the keys you send are changed, and a key that is not listed below is rejected "
                "rather than ignored. Published chatbot versions keep the content they were "
                "published with; publish the chatbot again to pick up the edit."
            ),
            tags=[tag],
        ),
        destroy=extend_schema(
            operation_id=f"{operation_prefix}_archive",
            parameters=[id_parameter],
            summary=f"Archive {noun}",
            description=(
                f"Archive the {noun.lower()}. This is a soft delete: it is hidden rather than "
                "destroyed, and a person can restore it in the web app. A repeat of this call "
                f"answers `404`, which makes it safe to retry. {archive_refusal}"
            ),
            tags=[tag],
            request=None,
            responses={
                200: ContentArchivedSerializer,
                404: OpenApiResponse(description="No such resource, or it is archived already."),
                409: ArchiveRefusedSerializer,
            },
        ),
    )


@_content_schema(
    noun="Source Material",
    plural="Source Material",
    archive_refusal=(
        "It is refused with `409` while a pipeline node or a live chatbot version still uses it; "
        "remove the reference from each node first."
    ),
)
class SourceMaterialViewSet(ContentViewSet):
    serializer_class = SourceMaterialResourceSerializer
    model = SourceMaterial

    def archive(self, instance: SourceMaterial) -> None:
        if not instance.archive():
            raise ArchiveRefused(
                "This source material is still used by a pipeline node or a live chatbot version. "
                "Remove it from those nodes, then archive again."
            )


@_content_schema(
    noun="Consent Form",
    plural="Consent Forms",
    archive_refusal=(
        "Every chatbot using the form, published versions included, is moved onto the team's "
        "default consent form. The default form itself cannot be archived and answers `409`."
    ),
)
class ConsentFormViewSet(ContentViewSet):
    serializer_class = ConsentFormResourceSerializer
    model = ConsentForm

    def archive(self, instance: ConsentForm) -> None:
        if not instance.archive():
            raise ArchiveRefused("The team's default consent form cannot be archived.")
