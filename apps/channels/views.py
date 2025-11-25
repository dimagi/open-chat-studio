import json
import uuid
from functools import cached_property

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, UpdateView
from django_htmx.http import HttpResponseClientRedirect
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import verify_hmac
from apps.channels import tasks
from apps.channels.exceptions import ExperimentChannelException
from apps.channels.forms import ChannelFormWrapper
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.serializers import (
    ApiMessageSerializer,
    ApiResponseMessageSerializer,
    CommCareConnectMessageSerializer,
)
from apps.channels.utils import validate_platform_availability
from apps.experiments.models import Experiment, ExperimentSession, ParticipantData
from apps.experiments.views.utils import get_channels_context
from apps.teams.decorators import login_and_team_required
from apps.web.waf import WafRule, waf_allow


@waf_allow(WafRule.NoUserAgent_HEADER)
@csrf_exempt
def new_telegram_message(request, channel_external_id: uuid):
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token != settings.TELEGRAM_SECRET_TOKEN:
        return HttpResponseBadRequest("Invalid request.")

    data = json.loads(request.body)
    tasks.handle_telegram_message.delay(message_data=data, channel_external_id=channel_external_id)
    return HttpResponse()


@csrf_exempt
@require_POST
def new_twilio_message(request):
    message_data = json.dumps(request.POST.dict())
    tasks.handle_twilio_message.delay(
        message_data=message_data,
        request_uri=request.build_absolute_uri(),
        signature=request.headers.get("X-Twilio-Signature"),
    )
    return HttpResponse()


@waf_allow(WafRule.NoUserAgent_HEADER)
@csrf_exempt
@require_POST
def new_sureadhere_message(request, sureadhere_tenant_id: int):
    message_data = json.loads(request.body)
    tasks.handle_sureadhere_message.delay(sureadhere_tenant_id=sureadhere_tenant_id, message_data=message_data)
    return HttpResponse()


@csrf_exempt
def new_turn_message(request, experiment_id: uuid):
    message_data = json.loads(request.body.decode("utf-8"))
    if "messages" not in message_data:
        # Normal inbound messages should have a "messages" key, so ignore everything else
        return HttpResponse()

    tasks.handle_turn_message.delay(experiment_id=experiment_id, message_data=message_data)
    return HttpResponse()


def new_api_message_schema(versioned: bool):
    operation_id = "new_api_message"
    summary = "New API Message"
    parameters = [
        OpenApiParameter(
            name="experiment_id",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description="Experiment ID",
        ),
    ]

    if versioned:
        operation_id = f"{operation_id}_versioned"
        summary = "New API Message Versioned"
        parameters.append(
            OpenApiParameter(
                name="version",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Version of experiment",
            )
        )

    return extend_schema(
        operation_id=operation_id,
        summary=summary,
        tags=["Channels"],
        request=ApiMessageSerializer(),
        responses={200: ApiResponseMessageSerializer()},
        parameters=parameters,
    )


@new_api_message_schema(versioned=False)
class NewApiMessageView(APIView):
    required_scopes = ("chatbots:interact",)

    def post(self, request, experiment_id: uuid):
        return _new_api_message(request, experiment_id)


@new_api_message_schema(versioned=True)
class NewApiMessageVersionedView(APIView):
    required_scopes = ("chatbots:interact",)

    def post(self, request, experiment_id: uuid, version=None):
        return _new_api_message(request, experiment_id, version)


def _new_api_message(request, experiment_id: uuid, version=None):
    """Chat with an experiment."""
    message_data = request.data.copy()
    participant_id = request.user.email

    session = None
    if session_id := message_data.get("session"):
        try:
            experiment = Experiment.objects.get(public_id=experiment_id)
            session = ExperimentSession.objects.select_related("experiment", "experiment_channel").get(
                external_id=session_id,
                experiment=experiment,
                team=request.team,
                participant__user=request.user,
                experiment_channel__platform=ChannelPlatform.API,
            )
        except ExperimentSession.DoesNotExist:
            raise Http404() from None
        participant_id = session.participant.identifier
        experiment_channel = session.experiment_channel
        experiment = session.experiment
    else:
        experiment = get_object_or_404(Experiment, public_id=experiment_id, team=request.team)
        experiment_channel = ExperimentChannel.objects.get_team_api_channel(request.team)
    experiment_version = experiment.get_version(version) if version is not None else experiment.default_version
    ai_response = tasks.handle_api_message(
        request.user,
        experiment_version,
        experiment_channel,
        message_data["message"],
        participant_id,
        session,
    )

    attachments = []
    if attached_files := ai_response.get_attached_files():
        attachments = [
            {"file_name": file.name, "link": file.download_link(ai_response.chat.experiment_session.id)}
            for file in attached_files
        ]

    return Response(
        data={
            "response": ai_response.content,
            "attachments": attachments,
        }
    )


@waf_allow(WafRule.SizeRestrictions_BODY)
@require_POST
@csrf_exempt
@verify_hmac
def new_connect_message(request: HttpRequest):
    serializer = CommCareConnectMessageSerializer(data=json.loads(request.body))
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)

    connect_channel_id = serializer.data["channel_id"]
    try:
        participant_data = ParticipantData.objects.get(
            system_metadata__commcare_connect_channel_id=connect_channel_id,
        )

        channel = ExperimentChannel.objects.get(
            platform=ChannelPlatform.COMMCARE_CONNECT, experiment__id=participant_data.experiment_id
        )
    except ParticipantData.DoesNotExist:
        return JsonResponse({"detail": "No participant data found"}, status=404)
    except ExperimentChannel.DoesNotExist:
        return JsonResponse({"detail": "No experiment channel found"}, status=404)

    if not participant_data.has_consented():
        return JsonResponse({"detail": "User has not given consent"}, status=status.HTTP_400_BAD_REQUEST)

    tasks.handle_commcare_connect_message.delay(
        experiment_channel_id=channel.id, participant_data_id=participant_data.id, messages=serializer.data["messages"]
    )
    return HttpResponse()


class BaseChannelDialogView(View):
    model = ExperimentChannel
    form_class = ChannelFormWrapper
    template_name = "chatbots/partials/channel_dialog.html"

    @cached_property
    def experiment(self):
        return get_object_or_404(
            Experiment.objects.select_related("team"),
            id=self.kwargs["experiment_id"],
            team__slug=self.kwargs["team_slug"],
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["experiment"] = self.experiment
        channel = kwargs.pop("instance", None)
        kwargs["channel"] = channel
        if channel:
            kwargs["platform"] = channel.platform_enum
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get("form")

        platform_form = form.channel_form
        extra_form = form.extra_form if hasattr(form, "extra_form") else None
        context.update(
            {
                "experiment": self.experiment,
                "form": platform_form,
                "extra_form": extra_form,
            }
        )
        if form.success_message:
            context["success_message"] = form.success_message
        if form.warning_message:
            context["warning_message"] = form.warning_message
        return context

    def get_success_url(self):
        team_slug = self.kwargs["team_slug"]
        experiment_id = self.kwargs["experiment_id"]
        return get_redirect_url(team_slug, experiment_id)

    def form_valid(self, form):
        channel = form.save()
        if form.success_message or form.warning_message:
            channels, available_platforms = get_channels_context(self.experiment)
            additional_context = {
                "save_successful": True,
                "channels": channels,
                "platforms": available_platforms,
                "channel": channel,
                "extra_form": channel.extra_form(
                    experiment=self.experiment
                ),  # override extra form to get 'update' rendering
            }
            return self.render_to_response({**self.get_context_data(form=form), **additional_context})
        return HttpResponseClientRedirect(self.get_success_url())


class ChannelEditDialogView(BaseChannelDialogView, PermissionRequiredMixin, UpdateView):
    """View for editing existing channels using UpdateView"""

    pk_url_kwarg = "channel_id"
    permission_required = "bot_channels.change_experimentchannel"

    def get_object(self, queryset=None):
        return get_object_or_404(
            ExperimentChannel,
            id=self.kwargs["channel_id"],
            experiment__id=self.kwargs["experiment_id"],
            team__slug=self.kwargs["team_slug"],
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["channel"] = self.object
        return context


class ChannelCreateDialogView(BaseChannelDialogView, PermissionRequiredMixin, CreateView):
    """View for creating new channels using CreateView"""

    permission_required = "bot_channels.add_experimentchannel"

    def get_platform(self):
        try:
            return ChannelPlatform(self.kwargs["platform_value"])
        except ValueError:
            raise Http404("Platform not found.") from None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["platform"] = self.get_platform()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["platform"] = self.get_platform()
        return context

    def get(self, request, *args, **kwargs):
        """Handle GET request with validation"""
        platform_enum = self.get_platform()

        try:
            validate_platform_availability(self.experiment, platform_enum)
        except ExperimentChannelException as e:
            messages.error(self.request, str(e))
            return redirect(self.get_success_url())

        return super().get(request, *args, **kwargs)


def get_redirect_url(team_slug: str, experiment_id: int) -> str:
    return reverse("chatbots:single_chatbot_home", args=[team_slug, experiment_id])


@login_and_team_required
@permission_required("bot_channels.delete_experimentchannel")
def delete_channel(request, team_slug, experiment_id: int, channel_id: int):
    channel = get_object_or_404(
        ExperimentChannel.objects.select_related("experiment"),
        id=channel_id,
        experiment__id=experiment_id,
        team__slug=team_slug,
    )
    channel.soft_delete()
    channels, available_platforms = get_channels_context(channel.experiment)
    return render(
        request,
        "chatbots/partials/channel_buttons_oob.html",
        {
            "channels": channels,
            "platforms": available_platforms,
            "experiment": channel.experiment,
        },
    )
