from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _


class ViewException(Exception):
    def __init__(self, html_message):
        self.html_message = html_message
        super().__init__(self.html_message)


class ChannelAlreadyUtilizedException(ViewException):
    def __init__(self, html_message="This channel is already being used by another chatbot."):
        super().__init__(html_message)

    @classmethod
    def get_message_for_channel(cls, channel, message_template=None):
        message_template = message_template or _("This channel is already being used by {}.")
        url = reverse(
            "chatbots:single_chatbot_home",
            kwargs={"team_slug": channel.team.slug, "experiment_id": channel.experiment_id},
        )
        link = format_html(_("<a href={}><u>another chatbot</u></a>"), url)
        return format_html(message_template, link)
