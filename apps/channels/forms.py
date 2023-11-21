from django import forms

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.teams.models import Team


class ChannelForm(forms.ModelForm):
    class Meta:
        model = ExperimentChannel
        fields = ["name", "platform", "messaging_provider"]
        widgets = {"platform": forms.HiddenInput()}

    def __init__(self, *args, **kwargs):
        team = kwargs.pop("team", None)
        super().__init__(*args, **kwargs)
        if self.is_bound:
            return
        platform = self.initial["platform"]
        self._populate_available_message_providers(team, platform)

    def _populate_available_message_providers(self, team: Team, platform: ChannelPlatform):
        provider_types = MessagingProviderType.platform_supported_provider_types(platform)
        queryset = MessagingProvider.objects.filter(team=team)
        # We must let the default queryset filter for the specific team
        self.fields["messaging_provider"].queryset = queryset
        if provider_types:
            self.fields["messaging_provider"].queryset = queryset.filter(type__in=provider_types)
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


class FacebookChannelForm(forms.Form):
    page_id = forms.CharField(label="Page ID", max_length=100)
    page_access_token = forms.CharField(label="Page Access Token")
    verify_token = forms.CharField(
        label="Verify Token", max_length=100, widget=forms.TextInput(attrs={"readonly": "readonly"})
    )
    webook_url = forms.CharField(
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
        label="Webhook URL",
        disabled=True,
        help_text="Use this as the webhook URL when setting up your Facebook App",
    )
