"""The team's resources: source material and consent forms (#4145).

Only working versions are reachable. Published chatbots hold their own snapshot of each resource, so
an edit here reaches them only once the chatbot is published again. A delete request archives the
resource rather than destroying it.
"""

from collections import defaultdict

from django.db import models, transaction
from rest_framework import mixins
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import BASE_PERMISSION_CLASSES, DjangoModelPermissionsWithView
from apps.api.v2.resources.exceptions import ArchiveRefused, SourceMaterialInUse
from apps.api.v2.resources.permissions import ReadOnlyForLimitedMachineTokens
from apps.api.v2.resources.schema import consent_form_schema, source_material_schema
from apps.api.v2.resources.serializers import (
    ConsentFormResourceSerializer,
    SourceMaterialResourceSerializer,
)
from apps.api.v2.write.base import DescribesPatch
from apps.api.v2.write.serializers import ResourceArchivedSerializer
from apps.experiments.models import ConsentForm, Experiment, SourceMaterial
from apps.oauth.permissions import TokenHasOAuthResourceScope
from apps.pipelines.models import Node, Pipeline


def _source_material_references(material: SourceMaterial) -> dict:
    """The nodes that stop ``material`` from being archived, grouped by the chatbot version holding them.

    Mirrors ``has_related_pipeline_references``: a live node using the material itself blocks
    wherever it is, and a node using one of its snapshots blocks only in a published or working
    chatbot.
    """
    snapshot_ids = list(material.versions.values_list("id", flat=True))
    current_chatbot_pipelines = material.get_related_experiments_queryset().values("pipeline_id")
    nodes = Node.objects.filter(
        models.Q(source_material_id=material.id)
        | models.Q(source_material_id__in=snapshot_ids, pipeline_id__in=current_chatbot_pipelines)
    )
    node_ids_by_pipeline = defaultdict(list)
    for pipeline_id, flow_id in nodes.order_by("id").values_list("pipeline_id", "flow_id"):
        node_ids_by_pipeline[pipeline_id].append(flow_id)
    chatbots = list(
        Experiment.objects.filter(team=material.team, pipeline_id__in=node_ids_by_pipeline)
        .select_related("working_version")
        .order_by("version_number")
    )
    listed_pipeline_ids = {chatbot.pipeline_id for chatbot in chatbots}
    other_pipeline_names = (
        Pipeline.objects.filter(id__in=set(node_ids_by_pipeline) - listed_pipeline_ids)
        .order_by("name")
        .values_list("name", flat=True)
        .distinct()
    )
    return {
        "chatbots": [
            {
                "chatbot_id": (chatbot.working_version or chatbot).public_id,
                "version_number": chatbot.version_number,
                "published": chatbot.is_default_version,
                "is_working_version": chatbot.is_working_version,
                "node_ids": node_ids_by_pipeline[chatbot.pipeline_id],
            }
            for chatbot in chatbots
        ],
        "other_pipelines": [{"name": name} for name in other_pipeline_names],
    }


class BaseResourceViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    """List, retrieve, create, patch and archive one team-scoped, versioned resource model."""

    permission_classes = [
        *BASE_PERMISSION_CLASSES,
        DjangoModelPermissionsWithView,
        TokenHasOAuthResourceScope,
        ReadOnlyForLimitedMachineTokens,
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
        return Response(ResourceArchivedSerializer({"archived": True}).data)

    def archive(self, instance) -> None:
        """Archive ``instance``, or raise ``ArchiveRefused``."""
        raise NotImplementedError


@source_material_schema
class SourceMaterialViewSet(BaseResourceViewSet):
    serializer_class = SourceMaterialResourceSerializer
    model = SourceMaterial

    def archive(self, instance: SourceMaterial) -> None:
        if not instance.archive():
            raise SourceMaterialInUse(references=_source_material_references(instance))


@consent_form_schema
class ConsentFormViewSet(BaseResourceViewSet):
    serializer_class = ConsentFormResourceSerializer
    model = ConsentForm

    def archive(self, instance: ConsentForm) -> None:
        if not instance.archive():
            raise ArchiveRefused("The team's default consent form cannot be archived.")
