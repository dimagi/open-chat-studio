from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from rest_framework import filters, mixins, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.api.permissions import DjangoModelPermissionsWithView, HasUserAPIKey
from apps.api.serializers import ExperimentSerializer, ExperimentSessionCreateSerializer, ExperimentSessionSerializer
from apps.experiments.models import Experiment, ExperimentSession, Participant, ParticipantData


class ExperimentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    permission_classes = [HasUserAPIKey, DjangoModelPermissionsWithView]
    serializer_class = ExperimentSerializer
    lookup_field = "public_id"

    def get_queryset(self):
        return Experiment.objects.filter(team__slug=self.request.team.slug).all()


@api_view(["POST"])
@permission_classes([HasUserAPIKey])
@permission_required("experiments.change_participantdata")
def update_participant_data(request, participant_id: str):
    """
    Upsert participant data for all specified experiments in the payload
    """
    experiment_data = request.data
    experiment_ids = experiment_data.keys()
    experiments = Experiment.objects.filter(public_id__in=experiment_ids, team=request.team)
    experiment_map = {str(experiment.public_id): experiment for experiment in experiments}
    participant = get_object_or_404(Participant, identifier=participant_id, team=request.team)

    missing_ids = set(experiment_ids) - set(experiment_map)
    if missing_ids:
        response = {"errors": [{"message": f"Experiment {experiment_id} not found"} for experiment_id in missing_ids]}
        return JsonResponse(data=response, status=404)

    for experiment_id, new_data in experiment_data.items():
        experiment = experiment_map[experiment_id]

        ParticipantData.objects.update_or_create(
            participant=participant,
            content_type__model="experiment",
            object_id=experiment.id,
            team=request.team,
            defaults={"team": experiment.team, "data": new_data, "content_object": experiment},
        )
    return HttpResponse()


class ExperimentSessionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    permission_classes = [HasUserAPIKey, DjangoModelPermissionsWithView]
    serializer_class = ExperimentSessionSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]
    lookup_field = "external_id"

    def get_queryset(self):
        return ExperimentSession.objects.filter(team__slug=self.request.team.slug).all()

    def create(self, request, *args, **kwargs):
        serializer = ExperimentSessionCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save()
        output = ExperimentSessionSerializer(instance=serializer.instance, context=self.get_serializer_context()).data
        headers = {"Location": str(output["url"])}
        return Response(output, status=status.HTTP_201_CREATED, headers=headers)
