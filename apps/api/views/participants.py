from datetime import timedelta

from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.decorators import api_view
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from apps.api.serializers import ParticipantDataUpdateRequest
from apps.api.tasks import setup_connect_channels_for_bots
from apps.channels.models import ChannelPlatform
from apps.events.models import ScheduledMessage, TimePeriod
from apps.experiments.models import Experiment, ExperimentSession, Participant, ParticipantData


@extend_schema(
    operation_id="update_participant_data",
    summary="Update Participant Data",
    tags=["Participants"],
    request=ParticipantDataUpdateRequest(),
    responses={200: {}},
    examples=[
        OpenApiExample(
            name="CreateParticipantData",
            summary="Create participant data for multiple experiments",
            value={
                "identifier": "part1",
                "platform": "api",
                "data": [
                    {"experiment": "exp1", "data": {"name": "John"}},
                    {
                        "experiment": "exp2",
                        "data": {"name": "Doe"},
                        "schedules": [
                            {
                                "id": "sched1",
                                "name": "Schedule 1",
                                "date": "2022-01-01T00:00:00Z",
                                "prompt": "Prompt 1",
                            },
                        ],
                    },
                ],
            },
        ),
        OpenApiExample(
            name="UpdateParticipantSchedules",
            summary="Update and delete participant schedules",
            value={
                "identifier": "part1",
                "platform": "api",
                "data": [
                    {
                        "experiment": "exp1",
                        "schedules": [
                            {
                                "id": "sched1",
                                "name": "Schedule 1 updated",
                                "date": "2022-01-01T00:00:00Z",
                                "prompt": "Prompt updated",
                            },
                            {"id": "sched2", "delete": True},
                        ],
                    },
                ],
            },
        ),
    ],
)
@api_view(["POST"])
@permission_required("experiments.change_participantdata")
def update_participant_data(request):
    return _update_participant_data(request)


@extend_schema(exclude=True)
@api_view(["POST"])
@permission_required("experiments.change_participantdata")
def update_participant_data_old(request):
    # This endpoint is kept for backwards compatibility of the path with a trailing "/"
    return _update_participant_data(request)


def _update_participant_data(request):
    """
    Upsert participant data for all specified experiments in the payload
    """
    serializer = ParticipantDataUpdateRequest(data=request.data)
    serializer.is_valid(raise_exception=True)

    identifier = serializer.data["identifier"]
    platform = serializer.data["platform"]
    team = request.team
    participant, _ = Participant.objects.get_or_create(identifier=identifier, team=team, platform=platform)

    # Update the participant's name if provided
    participant.update_name_from_data(serializer.data)

    experiment_data = serializer.data["data"]
    experiment_map = _get_participant_experiments(team, experiment_data)

    experiment_data_map = {}
    for data in experiment_data:
        experiment = experiment_map[data["experiment"]]

        participant_data, _created = ParticipantData.objects.update_or_create(
            participant=participant,
            experiment=experiment,
            team=team,
            defaults={"data": data["data"]} if data.get("data") else {},
        )

        if schedule_data := data.get("schedules"):
            _create_update_schedules(request, experiment, participant, schedule_data)

        if platform == ChannelPlatform.COMMCARE_CONNECT:
            experiment_data_map[experiment.id] = participant_data.id

    if platform == ChannelPlatform.COMMCARE_CONNECT:
        setup_connect_channels_for_bots.delay(connect_id=identifier, experiment_data_map=experiment_data_map)

    return HttpResponse()


def _get_participant_experiments(team, experiment_data) -> dict[str, Experiment]:
    experiment_ids = {data["experiment"] for data in experiment_data}
    experiments = Experiment.objects.filter(public_id__in=experiment_ids, team=team)
    experiment_map = {str(experiment.public_id): experiment for experiment in experiments}

    missing_ids = experiment_ids - set(experiment_map)
    if missing_ids:
        response = {"errors": [{"message": f"Experiment {experiment_id} not found"} for experiment_id in missing_ids]}
        raise NotFound(detail=response)

    return experiment_map


@transaction.atomic()
def _create_update_schedules(request, experiment, participant, schedule_data):
    def _get_id(data):
        return data.get("id") or ScheduledMessage.generate_external_id(data["name"], experiment.id, participant.id)

    data_by_id = {_get_id(data): data for data in schedule_data if not data.get("delete")}
    existing_by_id = {
        message.external_id: message
        for message in ScheduledMessage.objects.filter(
            external_id__in=data_by_id.keys(), participant=participant, experiment=experiment
        )
    }
    new = []
    updated = []
    for external_id, data in data_by_id.items():
        if external_id in existing_by_id:
            message = existing_by_id.get(external_id)
            message.next_trigger_date = data["date"]
            message.custom_schedule_params["name"] = data["name"]
            message.custom_schedule_params["prompt_text"] = data["prompt"]
            updated.append(message)
        else:
            new.append(
                ScheduledMessage(
                    team=request.team,
                    experiment=experiment,
                    participant=participant,
                    next_trigger_date=data["date"],
                    external_id=external_id,
                    custom_schedule_params={
                        "name": data["name"],
                        "prompt_text": data["prompt"],
                        "repetitions": 1,
                        # these aren't really needed since it's one-off schedule
                        "frequency": 1,
                        "time_period": TimePeriod.DAYS,
                    },
                )
            )

    delete_ids = {data["id"] for data in schedule_data if data.get("delete")}
    if delete_ids:
        ScheduledMessage.objects.filter(external_id__in=delete_ids).update(
            cancelled_at=timezone.now(), cancelled_by=request.user
        )
    if updated:
        ScheduledMessage.objects.bulk_update(updated, fields=["next_trigger_date", "custom_schedule_params"])
    if new:
        ScheduledMessage.objects.bulk_create(new)


@extend_schema(
    operation_id="participant_analytics",
    summary="Get Participant Analytics",
    tags=["Participants"],
    parameters=[
        OpenApiParameter(
            name="days",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description="Number of days back to analyze (default: 30)",
            required=False,
        ),
        OpenApiParameter(
            name="experiment_id",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description="Filter by specific experiment ID",
            required=False,
        ),
    ],
    responses={
        200: {
            "type": "object",
            "properties": {
                "overview": {"type": "object"},
                "daily_stats": {"type": "array"},
                "user_engagement": {"type": "object"},
            },
        }
    },
)
@api_view(["GET"])
@permission_required("experiments.view_participantdata")
def participant_analytics(request):
    """Get analytics for participants and their engagement"""

    days_back = int(request.query_params.get("days", 30))
    start_date = timezone.now() - timedelta(days=days_back)
    sessions = ExperimentSession.objects.filter(team=request.team, created_at__gte=start_date)
    experiment_id = request.query_params.get("experiment_id")
    if experiment_id:
        sessions = sessions.filter(experiment__public_id=experiment_id)

    total_sessions = sessions.count()
    unique_remote_users = (
        sessions.filter(participant__remote_id__isnull=False).values("participant__remote_id").distinct().count()
    )

    anonymous_users = (
        sessions.filter(participant__remote_id__isnull=True).values("participant__identifier").distinct().count()
    )

    return_user_data = (
        sessions.filter(participant__remote_id__isnull=False)
        .values("participant__remote_id")
        .annotate(session_count=Count("id"))
    )

    return_users = return_user_data.filter(session_count__gt=1).count()

    engagement_levels = {
        "single_session": return_user_data.filter(session_count=1).count(),
        "low_engagement": return_user_data.filter(session_count__range=[2, 5]).count(),
        "medium_engagement": return_user_data.filter(session_count__range=[6, 20]).count(),
        "high_engagement": return_user_data.filter(session_count__gt=20).count(),
    }

    daily_stats = []
    for i in range(min(days_back, 30)):
        day_start = start_date + timedelta(days=i)
        day_end = day_start + timedelta(days=1)

        day_sessions = sessions.filter(created_at__gte=day_start, created_at__lt=day_end)
        daily_stats.append(
            {
                "date": day_start.date().isoformat(),
                "sessions": day_sessions.count(),
                "unique_remote_users": day_sessions.filter(participant__remote_id__isnull=False)
                .values("participant__remote_id")
                .distinct()
                .count(),
                "anonymous_users": day_sessions.filter(participant__remote_id__isnull=True)
                .values("participant__identifier")
                .distinct()
                .count(),
            }
        )
    top_participants = return_user_data.order_by("-session_count")[:10]

    return Response(
        {
            "overview": {
                "total_sessions": total_sessions,
                "unique_remote_users": unique_remote_users,
                "anonymous_users": anonymous_users,
                "return_users": return_users,
                "return_user_rate": (return_users / unique_remote_users * 100) if unique_remote_users > 0 else 0,
            },
            "user_engagement": engagement_levels,
            "daily_stats": daily_stats,
            "top_participants": [
                {"remote_id": p["participant__remote_id"], "session_count": p["session_count"]}
                for p in top_participants
            ],
            "date_range": {
                "start": start_date.date().isoformat(),
                "end": timezone.now().date().isoformat(),
                "days": days_back,
            },
        }
    )


@extend_schema(
    operation_id="participant_details",
    summary="Get Participant Details by Remote ID",
    tags=["Participants"],
    parameters=[
        OpenApiParameter(
            name="remote_id",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description="Remote ID of the participant",
            required=True,
        ),
    ],
)
@api_view(["GET"])
@permission_required("experiments.view_participantdata")
def participant_details(request):
    """Get detailed information about a specific participant by remote_id"""

    remote_id = request.query_params.get("remote_id")
    if not remote_id:
        return Response({"error": "remote_id parameter is required"}, status=400)
    try:
        participant = Participant.objects.get(remote_id=remote_id, team=request.team)
    except Participant.DoesNotExist:
        return Response({"error": "Participant not found"}, status=404)

    sessions = (
        ExperimentSession.objects.filter(participant=participant, team=request.team)
        .select_related("experiment")
        .order_by("-created_at")
    )

    participant_data = ParticipantData.objects.filter(participant=participant, team=request.team).select_related(
        "experiment"
    )

    return Response(
        {
            "participant": {
                "remote_id": participant.remote_id,
                "identifier": participant.identifier,
                "name": participant.name,
                "platform": participant.platform,
                "created_at": participant.created_at.isoformat(),
            },
            "sessions": [
                {
                    "session_id": str(session.external_id),
                    "experiment_name": session.experiment.name,
                    "experiment_id": str(session.experiment.public_id),
                    "created_at": session.created_at.isoformat(),
                    "status": session.status,
                    "message_count": session.chat.messages.count(),
                }
                for session in sessions[:20]  # Limit to last 20 sessions
            ],
            "experiments": [
                {
                    "experiment_id": str(pd.experiment.public_id),
                    "experiment_name": pd.experiment.name,
                    "data": pd.data,
                    "last_updated": pd.updated_at.isoformat(),
                }
                for pd in participant_data
            ],
            "summary": {
                "total_sessions": sessions.count(),
                "experiments_participated": participant_data.count(),
                "first_session": sessions.last().created_at.isoformat() if sessions.exists() else None,
                "last_session": sessions.first().created_at.isoformat() if sessions.exists() else None,
            },
        }
    )
