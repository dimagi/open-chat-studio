from functools import cached_property

from django import forms

from apps.channels.const import SLACK_ALL_CHANNELS
from apps.channels.exceptions import ExperimentChannelException
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


class ExtraFormBase(forms.Form):
    form_attrs = {}
    """Additional HTML attributes to be added to the form element"""

    @cached_property
    def messaging_provider(self) -> MessagingProvider | None:
        if provider_id := self.data.get("messaging_provider"):
            return MessagingProvider.objects.filter(id=provider_id).first()

    def post_save(self, channel: ExperimentChannel):
        """Override this method to perform any additional actions after the channel has been saved"""
        pass

    def get_success_message(self, channel: ExperimentChannel):
        pass


class TelegramChannelForm(ExtraFormBase):
    bot_token = forms.CharField(label="Bot Token", max_length=100)


class WhatsappChannelForm(ExtraFormBase):
    number = forms.CharField(label="Number", max_length=100)
    webook_url = forms.CharField(
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
        label="Webhook URL",
        disabled=True,
        required=False,
        help_text="Use this as the URL when setting up the webhook",
    )

    def __init__(self, *args, **kwargs):
        initial = kwargs.get("initial", {})
        channel: ExperimentChannel = kwargs.pop("channel", None)
        if channel:
            initial["webook_url"] = channel.webhook_url
            kwargs["initial"] = initial

        super().__init__(*args, **kwargs)
        if not channel:
            # We only show the webhook URL field when there is something to show
            self.fields["webook_url"].widget = forms.HiddenInput()

    def get_success_message(self, channel: ExperimentChannel):
        """The message to be displayed when the channel is successfully linked"""
        return f"Use the following URL when setting up the webhook: {channel.webhook_url}"


class FacebookChannelForm(ExtraFormBase):
    page_id = forms.CharField(label="Page ID", max_length=100)
    webook_url = forms.CharField(
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
        label="Webhook URL",
        disabled=True,
        required=False,
        help_text="Use this as the URL when setting up the webhook",
    )

    def __init__(self, *args, **kwargs):
        initial = kwargs.get("initial", {})
        channel: ExperimentChannel = kwargs.pop("channel", None)
        if channel:
            initial["webook_url"] = channel.webhook_url
            kwargs["initial"] = initial

        super().__init__(*args, **kwargs)
        if not channel:
            # We only show the webhook URL field when there is something to show
            self.fields["webook_url"].widget = forms.HiddenInput()

    def get_success_message(self, channel: ExperimentChannel):
        """The message to be displayed when the channel is successfully linked"""
        return f"Use the following URL when setting up the webhook: {channel.webhook_url}"


class SlackChannelForm(ExtraFormBase):
    channel_mode = forms.ChoiceField(
        label="Channel Mode",
        choices=[("channel", "Listen on a specific channel"), ("all", "Listen on all unassigned channels")],
        widget=forms.RadioSelect(attrs={"x-model": "channelMode"}),
    )
    slack_channel_name = forms.CharField(
        label="Slack Channel",
        max_length=100,
        widget=forms.TextInput(attrs={"control_attrs": {"x-show": "channelMode === 'channel'"}}),
        required=False,
    )
    slack_channel_id = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, *args, **kwargs):
        initial = kwargs.setdefault("initial", {})
        if initial.get("slack_channel_id") == SLACK_ALL_CHANNELS:
            initial["channel_mode"] = "all"
        else:
            initial["channel_mode"] = "channel"
        self.form_attrs = {"x-data": '{"channelMode": "%s"}' % initial["channel_mode"]}
        super().__init__(*args, **kwargs)

    def clean_slack_channel_name(self):
        name = self.cleaned_data["slack_channel_name"].strip()
        if name.startswith("#"):
            name = name[1:]
        return name

    def clean(self):
        if self.cleaned_data["channel_mode"] == "all":
            self.cleaned_data["slack_channel_id"] = SLACK_ALL_CHANNELS
            self.cleaned_data["slack_channel_name"] = SLACK_ALL_CHANNELS
        elif self.messaging_provider:
            service = self.messaging_provider.get_messaging_service()
            channel_name = self.cleaned_data["slack_channel_name"]
            channel = service.get_channel_by_name(channel_name)
            if not channel:
                raise forms.ValidationError(f"No channel found with name {channel_name}")
            self.cleaned_data["slack_channel_id"] = channel["id"]
        return self.cleaned_data

    def post_save(self, channel: ExperimentChannel):
        channel_id = self.cleaned_data["slack_channel_id"]
        if channel_id != SLACK_ALL_CHANNELS and self.messaging_provider:
            service = self.messaging_provider.get_messaging_service()
            try:
                service.join_channel(channel_id)
            except Exception as e:
                raise ExperimentChannelException("Failed to join the channel") from e
