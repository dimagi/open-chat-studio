from django import forms
from django.urls import reverse

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import Experiment
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.teams.models import Team
from apps.web.meta import absolute_url


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


class ExtraFormBase(forms.Form):
    def get_success_message(self, channel: ExperimentChannel):
        """The message to be displayed when the channel is successfully linked"""
        if channel.messaging_provider and channel.messaging_provider.type == MessagingProviderType.turnio:
            webhook_url = absolute_url(
                reverse("channels:new_turn_message", kwargs={"experiment_id": channel.experiment.public_id}),
                is_secure=True,
            )
            return f"Use the following URL when setting up the webhook in Turn.io: {webhook_url}"


class TelegramChannelForm(ExtraFormBase):
    bot_token = forms.CharField(label="Bot Token", max_length=100)


class WhatsappChannelForm(ExtraFormBase):
    number = forms.CharField(label="Number", max_length=100)


class TurnIOForm(ExtraFormBase):
    number = forms.CharField(label="Number", max_length=100)
    webook_url = forms.CharField(
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
        label="Webhook URL",
        disabled=True,
        help_text="Use this as the URL when setting up the webhook in Turn.io",
    )

    def __init__(self, channel: ExperimentChannel, *args, **kwargs):
        webhook_url = absolute_url(
            reverse("channels:new_turn_message", kwargs={"experiment_id": channel.experiment.public_id}),
            is_secure=True,
        )
        initial = kwargs.get("initial", {})
        initial.setdefault("webook_url", webhook_url)
        kwargs["initial"] = initial
        return super().__init__(*args, **kwargs)


class FacebookChannelForm(ExtraFormBase):
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
