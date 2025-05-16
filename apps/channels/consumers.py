import json
import logging
import uuid

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.template.loader import render_to_string

from apps.annotations.models import TagCategories
from apps.channels.datamodels import BaseMessage
from apps.chat.channels import WebChannel
from apps.experiments.helpers import get_real_user_or_none
from apps.experiments.models import Experiment, ExperimentSession
from apps.teams.models import Team

logger = logging.getLogger(__name__)


class ChatbotConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        url_kwargs = self.scope["url_route"]["kwargs"]
        team_slug = url_kwargs["team_slug"]
        chatbot_id = url_kwargs.get("chatbot_id", None)
        session_id = url_kwargs.get("session_id", None)
        experiment_version = url_kwargs.get("experiment_version", None)

        if not (team_slug and chatbot_id and session_id):
            await self.close(code=400)
            return

        try:
            self.team = await Team.objects.aget(slug=team_slug)
        except Team.DoesNotExist:
            await self.close(code=404)
            return

        try:
            self.experiment = await Experiment.objects.get_all().aget(public_id=chatbot_id, team=self.team)
        except Experiment.DoesNotExist:
            await self.close(code=404)
            return

        if self.experiment.is_archived:
            await self.close(code=404)
            return

        if not self.experiment.is_working_version:
            await self.close(code=404)
            return

        if experiment_version and experiment_version != Experiment.DEFAULT_VERSION_NUMBER:
            try:
                if self.experiment.version_number == experiment_version:
                    self.experiment_version = self.experiment
                self.experiment_version = await Experiment.objects.aget(
                    working_version=self.experiment, version_number=experiment_version
                )
            except Experiment.DoesNotExist:
                await self.close(code=404)
                return
        elif self.experiment.is_default_version:
            self.experiment_version = self.experiment
        else:
            version = await Experiment.objects.filter(working_version=self.experiment, is_default_version=True).afirst()
            self.experiment_version = version if version else self.experiment

        if self.experiment_version.is_archived:
            await self.close(code=404)
            return

        if not self.experiment_version.is_public:
            await self.close(code=404)
            return

        self.user = get_real_user_or_none(self.scope["user"])
        # TODO: redirect for session state
        try:
            self.session = await ExperimentSession.objects.select_related(
                "experiment_channel", "participant", "chat"
            ).aget(
                experiment=self.experiment,
                external_id=session_id,
                team=self.team,
            )
        except ExperimentSession.DoesNotExist:
            await self.close(code=404)
            return
        await self.accept()

    async def disconnect(self, close_code):
        pass

    async def receive(self, text_data):
        text_data_json = json.loads(text_data)
        message_text = text_data_json["message"]
        # do nothing with empty messages
        if not message_text.strip():
            return

        await self.send(text_data=self._render_user_message(message_text))
        contents_div_id = f"message-response-{str(uuid.uuid4())}"
        await self.send(text_data=self._render_pending_response_message(contents_div_id))

        # TODO: error handling
        # TODO: send message to channel from events
        #   https://channels.readthedocs.io/en/latest/topics/channel_layers.html
        #   Probably use WebChannel.send_text_to_user
        chat_message = await sync_to_async(run_bot)(
            self.experiment,
            self.session,
            message_text,
        )
        await self.send(text_data=await self._render_bot_response(contents_div_id, chat_message))

    def _render_user_message(self, message_text):
        return render_to_string(
            "chatbots/websocket_components/user_message.html",
            self._get_context(message_text=message_text, attachments=[]),
        )

    def _render_pending_response_message(self, contents_div_id):
        return render_to_string(
            "chatbots/websocket_components/system_message.html",
            self._get_context(contents_div_id=contents_div_id, loading=True),
        )

    async def _render_bot_response(self, contents_div_id, message):
        kwargs = {"tags": []}
        if self.experiment.debug_mode_enabled:
            async for tag in message.tags.exclude(category=None).all():
                if tag.category == TagCategories.RESPONSE_RATING:
                    kwargs["message_rating"] = tag.value
                elif tag.category in (TagCategories.BOT_RESPONSE, TagCategories.SAFETY_LAYER_RESPONSE):
                    kwargs["tags"].append({"text": tag.badge_text, "status": tag.category_enum.badge_status})
        return render_to_string(
            "chatbots/websocket_components/final_system_message.html",
            self._get_context(message=message, contents_div_id=contents_div_id, **kwargs),
        )

    def _get_context(self, **kwargs):
        return {"experiment": self.experiment, "session": self.session, **kwargs}


def run_bot(experiment, session, message_text):
    web_channel = WebChannel(
        experiment,
        session.experiment_channel,
        experiment_session=session,
    )
    message = BaseMessage(
        participant_id=session.participant.identifier,
        message_text=message_text,
    )
    return web_channel.new_user_message(message)
