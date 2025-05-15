import asyncio
import json
import logging
import uuid

from channels.generic.websocket import AsyncWebsocketConsumer
from django.template.loader import render_to_string

from apps.experiments.models import Experiment, ExperimentSession
from apps.teams.models import Team

logger = logging.getLogger(__name__)


class BotAccessException(Exception):
    pass


class BotChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        url_kwargs = self.scope["url_route"]["kwargs"]
        team_slug = url_kwargs["team_slug"]
        chatbot_id = url_kwargs.get("chatbot_id", None)
        session_id = url_kwargs.get("session_id", None)

        user = self.scope["user"]

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

        # self.experiment_version = Experiment.objects.get_default_or_working(self)
        # if not self.experiment_version.is_public:
        #     await self.close(code=404)
        #     return

        if session_id:
            self.user = None if user.is_anonymous else user

            # TODO: redirect for session state
            try:
                self.session = await ExperimentSession.objects.select_related("participant", "chat").aget(
                    experiment=self.experiment,
                    external_id=session_id,
                    team=self.team,
                )
            except ExperimentSession.DoesNotExist:
                await self.close(code=404)
                return
        else:
            # TODO: additional checks
            self.session = None
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
        contents_div_id = str(uuid.uuid4())
        await self.send(text_data=self._render_pending_response_message(contents_div_id))
        await asyncio.sleep(2)
        await self.send(text_data=self._render_bot_response(contents_div_id, "Bot response here"))

    def _render_user_message(self, message_text):
        return render_to_string(
            "chatbots/websocket_components/user_message.html",
            self._get_context(message_text=message_text, attachments=[]),
        )

    def _render_pending_response_message(self, contents_div_id):
        return render_to_string(
            "chatbots/websocket_components/system_message.html", self._get_context(contents_div_id=contents_div_id)
        )

    def _render_bot_response(self, contents_div_id, content):
        return render_to_string(
            "chatbots/websocket_components/final_system_message.html",
            self._get_context(message_text=content, contents_div_id=contents_div_id),
        )

    def _get_context(self, **kwargs):
        return {"experiment": self.experiment, "session": self.session, **kwargs}
