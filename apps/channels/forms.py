from django import forms

from apps.channels.models import ExperimentChannel
from apps.service_providers.models import MessagingProvider, MessagingProviderType


class ChannelForm(forms.ModelForm):
    class Meta:
        model = ExperimentChannel
        fields = ["name", "platform", "messaging_provider"]
        widgets = {"platform": forms.HiddenInput()}

    def __init__(self, *args, **kwargs):
        team = kwargs.pop("team", None)
        super().__init__(*args, **kwargs)
        initial = kwargs.get("initial", {})
        platform = initial.get("platform", None)
        if platform:
            provider_types = MessagingProviderType.platform_supported_provider_types(platform)
            if provider_types:
                self.fields["messaging_provider"].queryset = MessagingProvider.objects.filter(
                    type__in=provider_types, team=team
                )
            else:
                self.fields["messaging_provider"].widget = forms.HiddenInput()

    def save(self, experiment, config_data: dict):
        self.instance.experiment = experiment
        self.instance.extra_data = config_data
        return super().save()


class TelegramChannelForm(forms.Form):
    bot_token = forms.CharField(label="Bot Token", max_length=100)


class WhatsappChannelForm(forms.Form):
    number = forms.CharField(label="Number", max_length=100)
