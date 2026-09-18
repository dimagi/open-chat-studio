from django import forms

from apps.channels.forms.base import ExtraFormBase


class CommCareConnectChannelForm(ExtraFormBase):
    commcare_connect_bot_name = forms.CharField(
        label="Bot Name",
        help_text="This is the name of the chatbot that will be displayed to users on CommCare Connect",
        max_length=100,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.channel:
            self.warning_message = (
                "Changing the bot name updates the name shown to new participants on CommCare Connect. "
                "Participants who have already connected may continue to see the previous name."
            )
