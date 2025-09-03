import json
import uuid

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.api.permissions import verify_hmac
from apps.channels import tasks
from apps.channels.exceptions import ExperimentChannelException
from apps.channels.forms import ChannelForm
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.serializers import (
    ApiMessageSerializer,
    ApiResponseMessageSerializer,
    CommCareConnectMessageSerializer,
)
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.experiments.models import Experiment, ExperimentSession, ParticipantData


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
@api_view(["POST"])
def new_api_message(request, experiment_id: uuid):
    return _new_api_message(request, experiment_id)


@new_api_message_schema(versioned=True)
@api_view(["POST"])
def new_api_message_versioned(request, experiment_id: uuid, version=None):
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
    template_name = "chatbots/partials/channel_dialog.html"

    def get_context_data(self, **kwargs):
        context = {
            "request": self.request,
            "team": self.request.team,
        }
        context.update(kwargs)
        return context

    def _redirect_based_on_origin(self, origin: str, team_slug: str, experiment_id: int):
        """Helper method for redirecting based on origin"""
        if origin == "chatbots":
            return redirect(reverse("chatbots:single_chatbot_home", args=[team_slug, experiment_id]))
        else:
            return redirect(reverse("experiments:single_experiment_home", args=[team_slug, experiment_id]))

    def _handle_form_errors(self, form, extra_form=None):
        """Handle form validation errors"""
        if form and not form.is_valid():
            messages.error(self.request, "Form has errors: " + form.errors.as_text())
            return True

        if extra_form and not extra_form.is_valid():
            messages.error(self.request, format_html("Channel data has errors: " + extra_form.errors.as_ul()))
            return True

        return False


class ChannelEditDialogView(BaseChannelDialogView):
    """View for editing existing channels"""

    def get(self, request, team_slug, experiment_id, channel_id):
        """Show the edit dialog"""
        channel = get_object_or_404(
            ExperimentChannel, id=channel_id, experiment__id=experiment_id, experiment__team__slug=team_slug
        )
        form = channel.form
        extra_form = channel.extra_form

        context = self.get_context_data(
            experiment=channel.experiment,
            channel=channel,
            form=form,
            extra_form=extra_form,
        )

        return render(request, self.template_name, context)

    def post(self, request, team_slug, experiment_id, channel_id):
        """Handle channel update/delete"""
        channel = get_object_or_404(
            ExperimentChannel, id=channel_id, experiment__id=experiment_id, experiment__team__slug=team_slug
        )
        origin = request.GET.get("origin")

        if request.POST.get("action") == "delete":
            return self._handle_delete(channel, origin, team_slug, experiment_id)

        return self._handle_update(channel, origin, team_slug, experiment_id)

    def _handle_delete(self, channel, origin, team_slug, experiment_id):
        """Handle channel deletion"""
        if not self.request.user.has_perm("bot_channels.delete_experimentchannel"):
            raise PermissionDenied

        channel.soft_delete()
        return self._redirect_based_on_origin(origin, team_slug, experiment_id)

    def _handle_update(self, channel, origin, team_slug, experiment_id):
        """Handle channel update"""
        if not self.request.user.has_perm("bot_channels.change_experimentchannel"):
            raise PermissionDenied

        form = channel.form(data=self.request.POST)
        extra_form = channel.extra_form(data=self.request.POST)

        if self._handle_form_errors(form, extra_form):
            return self._redirect_based_on_origin(origin, team_slug, experiment_id)

        config_data = {}
        if extra_form:
            config_data = extra_form.cleaned_data

        platform = ChannelPlatform(form.cleaned_data["platform"])
        channel_identifier = config_data[platform.channel_identifier_key]

        try:
            ExperimentChannel.check_usage_by_another_experiment(
                platform, identifier=channel_identifier, new_experiment=channel.experiment
            )
        except ChannelAlreadyUtilizedException as exception:
            messages.error(self.request, exception.html_message)
            return self._redirect_based_on_origin(origin, team_slug, experiment_id)

        form.save(channel.experiment, config_data)
        return self._redirect_based_on_origin(origin, team_slug, experiment_id)


class ChannelCreateDialogView(BaseChannelDialogView):
    def get(self, request, team_slug, experiment_id, platform_value):
        """Show the create dialog"""
        experiment = get_object_or_404(Experiment, id=experiment_id, team__slug=team_slug)

        try:
            platform_enum = ChannelPlatform(platform_value)
        except ValueError:
            raise Http404("Platform not found.")

        channels = experiment.experimentchannel_set.exclude(
            platform__in=[ChannelPlatform.WEB, ChannelPlatform.API, ChannelPlatform.EVALUATIONS]
        ).all()
        used_platforms = {channel.platform_enum for channel in channels}
        available_platforms = ChannelPlatform.for_dropdown(used_platforms, experiment.team)

        if not available_platforms.get(platform_enum):
            return HttpResponse("Invalid or unavailable platform.", status=400)

        platform_form = platform_enum.form(experiment=experiment)
        extra_form = platform_enum.extra_form()
        context = self.get_context_data(
            experiment=experiment,
            platform=platform_enum,
            platform_form=platform_form,
            extra_form=extra_form,
        )
        return render(request, self.template_name, context)

    def post(self, request, team_slug, experiment_id, platform_value):
        """Handle channel creation"""
        if not self.request.user.has_perm("bot_channels.add_experimentchannel"):
            raise PermissionDenied

        experiment = get_object_or_404(Experiment, id=experiment_id, team__slug=team_slug)
        origin = request.GET.get("origin")

        existing_platforms = {channel.platform_enum for channel in experiment.experimentchannel_set.all()}
        form = ChannelForm(experiment=experiment, data=request.POST)

        if self._handle_form_errors(form):
            return self._redirect_based_on_origin(origin, team_slug, experiment_id)

        platform = ChannelPlatform(form.cleaned_data["platform"])
        if platform in existing_platforms:
            messages.error(request, f"Channel for {platform.label} already exists")
            return self._redirect_based_on_origin(origin, team_slug, experiment_id)

        extra_form = platform.extra_form(data=request.POST)
        config_data = {}
        if extra_form:
            if self._handle_form_errors(None, extra_form):
                return self._redirect_based_on_origin(origin, team_slug, experiment_id)
            config_data = extra_form.cleaned_data

        try:
            ExperimentChannel.check_usage_by_another_experiment(
                platform, identifier=config_data[platform.channel_identifier_key], new_experiment=experiment
            )
        except ChannelAlreadyUtilizedException as exception:
            messages.error(request, exception.html_message)
            return self._redirect_based_on_origin(origin, team_slug, experiment_id)

        form.save(experiment, config_data)

        if extra_form:
            try:
                extra_form.post_save(channel=form.instance)
            except ExperimentChannelException as e:
                messages.error(request, "Error saving channel: " + str(e))
            else:
                if extra_form.success_message:
                    messages.info(request, extra_form.success_message)

                if extra_form.warning_message:
                    messages.warning(request, extra_form.warning_message)

        return self._redirect_based_on_origin(origin, team_slug, experiment_id)
