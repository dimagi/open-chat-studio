from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from oauth2_provider.contrib.rest_framework import TokenHasScope
from rest_framework import mixins
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import DjangoModelPermissionsWithView
from apps.api.serializers import ExperimentSerializer
from apps.experiments.models import Experiment


@extend_schema_view(
    list=extend_schema(
        operation_id="experiment_list",
        summary=settings.API_SUMMARIES["list_chatbots"],
        tags=["Experiments"],
    ),
    retrieve=extend_schema(
        operation_id="experiment_retrieve",
        summary=settings.API_SUMMARIES["retrieve_chatbot"],
        tags=["Experiments"],
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="Experiment ID",
            ),
        ],
    ),
)
class ExperimentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    permission_classes = [DjangoModelPermissionsWithView, TokenHasScope]
    serializer_class = ExperimentSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"

    def get_required_scopes(self, request, view):
        if self.action == "list":
            return ["list_experiments"]
        elif self.action == "retrieve":
            return ["retrieve_experiment"]
        return []

    def get_queryset(self):
        # Only return working experiments
        return Experiment.objects.filter(team__slug=self.request.team.slug).filter(working_version__isnull=True)
