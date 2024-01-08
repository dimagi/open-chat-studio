from typing import ClassVar

from django import forms
from django.forms import ValidationError
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _

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


class ChannelFormBase(forms.Form):
    """
    Base class for channel-specific forms.

    Attributes:
        channel_identifier_key (ClassVar): The key used to identify a specific channel in the `extra_data` json
            field on an `ExperimentChannel` instance. Subclasses need to specify this key and it will differ for
            each channel type (Telegram, Whatsapp etc..)
    """

    channel_identifier_key: ClassVar

    def clean(self):
        cleaned_data = super().clean()
        filter_params = {f"extra_data__{self.channel_identifier_key}": cleaned_data[self.channel_identifier_key]}

        channel = ExperimentChannel.objects.filter(**filter_params).first()
        if channel:
            experiment = channel.experiment
            url = reverse(
                "experiments:single_experiment_home",
                kwargs={"team_slug": experiment.team.slug, "experiment_id": experiment.id},
            )
            messsage = format_html(_("This channel is already used in <a href={}><u>another experiment</u></a>"), url)
            raise ValidationError(messsage, code="invalid")
        return cleaned_data


class TelegramChannelForm(ChannelFormBase):
    channel_identifier_key = "bot_token"
    bot_token = forms.CharField(label="Bot Token", max_length=100)


class WhatsappChannelForm(ChannelFormBase):
    channel_identifier_key = "number"
    number = forms.CharField(label="Number", max_length=100)


class FacebookChannelForm(ChannelFormBase):
    channel_identifier_key = "page_id"
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
