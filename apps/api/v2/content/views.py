"""The team's content resources: source material and consent forms (#4145).

Only working versions are reachable. Published chatbots hold their own snapshot of each resource, so
an edit here reaches them only once the chatbot is published again. Deleting archives rather than
destroys.
"""

from collections import defaultdict

from django.db import models, transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import mixins
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import BASE_PERMISSION_CLASSES, DjangoModelPermissionsWithView
from apps.api.v2.content.exceptions import ArchiveRefused, SourceMaterialInUse
from apps.api.v2.content.serializers import (
    ArchiveRefusedSerializer,
    ConsentFormResourceSerializer,
    ContentArchivedSerializer,
    SourceMaterialInUseSerializer,
    SourceMaterialResourceSerializer,
)
from apps.api.v2.write.base import DescribesPatch
from apps.experiments.models import ConsentForm, SourceMaterial
from apps.oauth.permissions import TokenHasOAuthResourceScope, is_client_credentials_request
from apps.pipelines.models import Node


def _source_material_references(material: SourceMaterial) -> dict:
    """The chatbot versions and other pipelines whose nodes use ``material`` or one of its versions."""
    chatbots = list(material.get_related_experiments_queryset().select_related("working_version").order_by("id"))
    material_ids = [*material.versions.values_list("id", flat=True), material.id]
    pipeline_ids = [chatbot.pipeline_id for chatbot in chatbots]
    node_ids_by_pipeline = defaultdict(list)
    for pipeline_id, flow_id in (
        Node.objects.filter(pipeline_id__in=pipeline_ids, source_material_id__in=material_ids)
        .order_by("id")
        .values_list("pipeline_id", "flow_id")
    ):
        node_ids_by_pipeline[pipeline_id].append(flow_id)
    other_pipeline_names = (
        material.get_related_nodes_queryset()
        .exclude(pipeline_id__in=node_ids_by_pipeline)
        .order_by("pipeline__name")
        .values_list("pipeline__name", flat=True)
        .distinct()
    )
    return {
        "chatbots": [
            {
                "chatbot_id": (chatbot.working_version or chatbot).public_id,
                "version_number": chatbot.version_number,
                "published": not chatbot.is_working_version,
                "node_ids": node_ids_by_pipeline[chatbot.pipeline_id],
            }
            for chatbot in chatbots
        ],
        "other_pipelines": [{"name": name} for name in other_pipeline_names],
    }


class ReadOnlyForMachineTokens(BasePermission):
    """Refuse writes from client-credentials (machine) tokens.

    A machine application is pinned to some of the team's chatbots, but content is shared by all of them.
    """

    message = "Client-credentials tokens cannot modify source material or consent forms."

    def has_permission(self, request, view) -> bool:
        return request.method in SAFE_METHODS or not is_client_credentials_request(request)


class BaseContentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    """List, retrieve, create, patch and archive one team-scoped, versioned content model."""

    permission_classes = [
        *BASE_PERMISSION_CLASSES,
        DjangoModelPermissionsWithView,
        TokenHasOAuthResourceScope,
        ReadOnlyForMachineTokens,
    ]
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


def _content_schema(
    *, noun: str, plural: str, archive_refusal: str, archive_refused_serializer=ArchiveRefusedSerializer
):
    """The OpenAPI descriptions shared by both content viewsets."""
    operation_prefix = noun.lower().replace(" ", "_")
    tag = plural
    machine_tokens = " A client-credentials token can read but not write; its writes answer `403`."
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
            description="A key that is not listed below is rejected rather than ignored." + machine_tokens,
            tags=[tag],
        ),
        partial_update=extend_schema(
            operation_id=f"{operation_prefix}_update",
            parameters=[id_parameter],
            summary=f"Update {noun}",
            description=(
                "Only the keys you send are changed, and a key that is not listed below is rejected "
                "rather than ignored. Published chatbot versions keep the content they were "
                "published with; publish the chatbot again to pick up the edit." + machine_tokens
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
                f"answers `404`, which makes it safe to retry. {archive_refusal}" + machine_tokens
            ),
            tags=[tag],
            request=None,
            responses={
                200: ContentArchivedSerializer,
                404: OpenApiResponse(description="No such resource, or it is archived already."),
                409: archive_refused_serializer,
            },
        ),
    )


@_content_schema(
    noun="Source Material",
    plural="Source Material",
    archive_refusal=(
        "It is refused with `409` while a pipeline node or a live chatbot version still uses it. "
        "The response lists each chatbot version and node that uses it."
    ),
    archive_refused_serializer=SourceMaterialInUseSerializer,
)
class SourceMaterialViewSet(BaseContentViewSet):
    serializer_class = SourceMaterialResourceSerializer
    model = SourceMaterial

    def archive(self, instance: SourceMaterial) -> None:
        if not instance.archive():
            raise SourceMaterialInUse(references=_source_material_references(instance))


@_content_schema(
    noun="Consent Form",
    plural="Consent Forms",
    archive_refusal=(
        "Every chatbot draft using the form is moved onto the team's default consent form; published "
        "versions keep the form they were published with. The default form itself cannot be archived "
        "and answers `409`."
    ),
)
class ConsentFormViewSet(BaseContentViewSet):
    serializer_class = ConsentFormResourceSerializer
    model = ConsentForm

    def archive(self, instance: ConsentForm) -> None:
        if not instance.archive():
            raise ArchiveRefused("The team's default consent form cannot be archived.")
