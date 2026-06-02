from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import DjangoModelPermissionsWithView
from apps.api.v2.serializers import ChatbotSerializer
from apps.experiments.models import Experiment
from apps.oauth.permissions import TokenHasOAuthResourceScope


@extend_schema_view(
    list=extend_schema(
        operation_id="chatbot_list",
        summary="List Chatbots",
        tags=["Chatbots"],
    ),
    retrieve=extend_schema(
        operation_id="chatbot_retrieve",
        summary="Retrieve Chatbot",
        tags=["Chatbots"],
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description="Chatbot ID",
            ),
        ],
    ),
)
class ChatbotViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    permission_classes = [DjangoModelPermissionsWithView, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]
    serializer_class = ChatbotSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        # Only the working (draft) version family heads are exposed at the top level.
        return Experiment.objects.filter(team=self.request.team).filter(working_version__isnull=True)
