from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import DjangoModelPermissionsWithView
from apps.api.serializers import ExperimentSerializer
from apps.experiments.models import Experiment
from apps.oauth.permissions import TokenHasOAuthResourceScope


@extend_schema_view(
    list=extend_schema(
        operation_id="experiment_list",
        summary="List Chatbots",
        tags=["Experiments"],
    ),
    retrieve=extend_schema(
        operation_id="experiment_retrieve",
        summary="Retrieve Chatbot",
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
    permission_classes = [DjangoModelPermissionsWithView, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]
    serializer_class = ExperimentSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        # Only return working experiments
        return Experiment.objects.filter(team__slug=self.request.team.slug).filter(working_version__isnull=True)
