from django import forms

from apps.channels.models import ExperimentChannel


class ChannelForm(forms.ModelForm):
    class Meta:
        model = ExperimentChannel
        fields = ["name", "platform", "messaging_provider"]
        widgets = {"platform": forms.HiddenInput()}

    def __init__(self, *args, **kwargs):
        team = kwargs.pop("team", None)
        super().__init__(*args, **kwargs)

    def save(self, experiment, config_data: dict):
        self.instance.experiment = experiment
        self.instance.extra_data = config_data
        return super().save()


class TelegramChannelForm(forms.Form):
    bot_token = forms.CharField(label="Bot Token", max_length=100)


class WhatsappChannelForm(forms.Form):
    number = forms.CharField(label="Number", max_length=100)
