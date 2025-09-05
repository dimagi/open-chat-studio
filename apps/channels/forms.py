import logging
from functools import cached_property

import phonenumbers
from django import forms
from django.conf import settings
from django.urls import reverse
from telebot import TeleBot, apihelper, types

from apps.channels.const import SLACK_ALL_CHANNELS
from apps.channels.exceptions import ExperimentChannelException
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.teams.models import Team
from apps.web.meta import absolute_url

logger = logging.getLogger("ocs.channels")


class ChannelFormWrapper(forms.Form):
    """
    A wrapper class that combines ChannelForm and platform-specific extra forms
    to work with Django's built-in CreateView and UpdateView.
    """

    def __init__(self, experiment, platform, channel=None, *args, **kwargs):
        self.experiment = experiment
        self.platform = platform
        self.channel = channel

        super().__init__(*args, **kwargs)

        try:
            self._init_forms(*args, **kwargs)
            self._combine_form_fields()
        except Exception as e:
            logger.warning(f"Form initialization failed: {e}")
            self.channel_form = None
            self.extra_form = None

    def _init_forms(self, *args, **kwargs):
        """Initialize the main channel form and extra form"""
        if self.channel:
            self.channel_form = self.channel.form(data=kwargs.get("data"))
            self.extra_form = self.channel.extra_form(data=kwargs.get("data"))
        else:
            if not self.platform:
                raise ValueError("Platform must be provided when creating a new channel")

            form_kwargs = {
                "experiment": self.experiment,
                "data": kwargs.get("data"),
                "initial": kwargs.get("initial", {}),
            }
            form_kwargs["initial"]["platform"] = self.platform.value

            self.channel_form = ChannelForm(**form_kwargs)
            self.extra_form = self.platform.extra_form(data=kwargs.get("data"))

    def _combine_form_fields(self):
        """Combine fields from both forms into this wrapper"""
        for field_name, field in self.channel_form.fields.items():
            self.fields[field_name] = field
        if self.extra_form:
            for field_name, field in self.extra_form.fields.items():
                self.fields[field_name] = field

    def clean(self):
        cleaned_data = super().clean()

        # Run cleaning on both forms
        self.channel_form.full_clean()
        if self.extra_form:
            self.extra_form.full_clean()

        # Merge cleaned data
        if hasattr(self.channel_form, "cleaned_data"):
            cleaned_data.update(self.channel_form.cleaned_data)
        if self.extra_form and hasattr(self.extra_form, "cleaned_data"):
            cleaned_data.update(self.extra_form.cleaned_data)

        platform = ChannelPlatform(cleaned_data["platform"])
        channel_identifier = cleaned_data.get(platform.channel_identifier_key, "")

        try:
            ExperimentChannel.check_usage_by_another_experiment(
                platform, identifier=channel_identifier, new_experiment=self.channel.experiment
            )
        except ChannelAlreadyUtilizedException as e:
            self.channel_form.add_error(None, e.html_message)

        return cleaned_data

    def is_valid(self):
        """Validate both forms"""
        channel_valid = self.channel_form.is_valid()
        extra_valid = self.extra_form.is_valid() if self.extra_form else True
        return channel_valid and extra_valid

    @property
    def errors(self):
        """Combine errors from both forms"""
        combined_errors = self.channel_form.errors.copy()
        if self.extra_form and self.extra_form.errors:
            combined_errors.update(self.extra_form.errors)
        return combined_errors

    @property
    def form_attrs(self):
        """Get form attributes from extra form if available"""
        return getattr(self.extra_form, "form_attrs", {})

    def save(self, commit=True):
        """Save both forms"""
        # Prepare config data from extra form
        config_data = {}
        if self.extra_form and self.extra_form.is_valid():
            config_data = self.extra_form.cleaned_data

        instance = self.channel_form.save(self.experiment, config_data)

        if self.extra_form and hasattr(self.extra_form, "post_save"):
            self.extra_form.post_save(channel=instance)

        return instance

    @property
    def success_message(self):
        """Get success message from extra form if available"""
        return getattr(self.extra_form, "success_message", "")

    @property
    def warning_message(self):
        """Get warning message from extra form if available"""
        return getattr(self.extra_form, "warning_message", "")


class ChannelForm(forms.ModelForm):
    name = forms.CharField(required=False, help_text="If you leave this blank, it will default to the experiment name")

    class Meta:
        model = ExperimentChannel
        fields = ["name", "platform", "messaging_provider"]
        widgets = {"platform": forms.HiddenInput()}

    def __init__(self, *args, **kwargs):
        experiment = kwargs.pop("experiment", None)
        initial: dict = kwargs.get("initial", {})
        initial.setdefault("name", experiment.name)
        super().__init__(*args, **kwargs)
        platform = self.initial["platform"]
        self._populate_available_message_providers(experiment.team, platform)

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
        self.instance.team = experiment.team
        self.instance.experiment = experiment
        self.instance.extra_data = config_data
        return super().save()


class ExtraFormBase(forms.Form):
    success_message = ""
    warning_message = ""
    form_attrs = {}
    """Additional HTML attributes to be added to the form element"""

    @cached_property
    def messaging_provider(self) -> MessagingProvider | None:
        if provider_id := self.data.get("messaging_provider"):
            return MessagingProvider.objects.filter(id=provider_id).first()

    def post_save(self, channel: ExperimentChannel):
        """Override this method to perform any additional actions after the channel has been saved"""
        pass


class WebhookUrlFormBase(ExtraFormBase):
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

    def post_save(self, channel: ExperimentChannel):
        self.success_message = f"Use the following URL when setting up the webhook: {channel.webhook_url}"


class TelegramChannelForm(ExtraFormBase):
    bot_token = forms.CharField(label="Bot Token", max_length=100)

    def post_save(self, channel: ExperimentChannel):
        try:
            self._set_telegram_webhook(channel)
        except apihelper.ApiTelegramException as e:
            logger.exception("Error setting Telegram webhook")
            raise ExperimentChannelException("Error setting Telegram webhook") from e

    def _set_telegram_webhook(self, experiment_channel: ExperimentChannel):
        """
        Set the webhook at Telegram to allow message forwarding to this platform
        """
        tele_bot = TeleBot(experiment_channel.extra_data.get("bot_token", ""), threaded=False)
        if experiment_channel.deleted:
            webhook_url = None
        else:
            webhook_url = absolute_url(reverse("channels:new_telegram_message", args=[experiment_channel.external_id]))

        tele_bot.set_webhook(webhook_url, secret_token=settings.TELEGRAM_SECRET_TOKEN)
        tele_bot.set_my_commands(commands=[types.BotCommand(ExperimentChannel.RESET_COMMAND, "Restart chat")])

    def clean_bot_token(self):
        """Checks the bot token by making a request to get info on the bot. If the token is invalid, an
        ApiTelegramException will be raised with error_code = 404
        """
        bot_token = self.cleaned_data["bot_token"]
        try:
            bot = TeleBot(bot_token, threaded=False)
            bot.get_me()
        except apihelper.ApiTelegramException as ex:
            if ex.error_code == 404:
                raise forms.ValidationError(f"Invalid token: {bot_token}") from None
            else:
                logger.exception(ex)
                raise forms.ValidationError("Could not verify the bot token") from None
        return bot_token


class WhatsappChannelForm(WebhookUrlFormBase):
    number = forms.CharField(
        label="Number",
        max_length=20,
        help_text=(
            "This is the WhatsApp Business Number you got from your provider and should be in any of the formats: "
            "+27812345678, +27-81-234-5678, +27 81 234 5678"
        ),
    )

    def clean_number(self):
        try:
            number_obj = phonenumbers.parse(self.cleaned_data["number"])
            number = phonenumbers.format_number(number_obj, phonenumbers.PhoneNumberFormat.E164)
            service = self.messaging_provider.get_messaging_service()
            if not service.is_valid_number(number):
                self.warning_message = (
                    f"{number} was not found at the provider. Please make sure it is there before proceeding"
                )
            return number
        except phonenumbers.NumberParseException:
            raise forms.ValidationError("Enter a valid phone number (e.g. +12125552368).") from None


class SureAdhereChannelForm(WebhookUrlFormBase):
    sureadhere_tenant_id = forms.CharField(
        label="SureAdhere Tenant ID", max_length=100, help_text="Enter the Tenant ID provided by SureAdhere."
    )


class FacebookChannelForm(WebhookUrlFormBase):
    page_id = forms.CharField(label="Page ID", max_length=100)


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
        self.form_attrs = {"x-data": '{{"channelMode": "{}"}}'.format(initial["channel_mode"])}
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


class CommCareConnectChannelForm(ExtraFormBase):
    commcare_connect_bot_name = forms.CharField(
        label="Bot Name",
        help_text="This is the name of the chatbot that will be displayed to users on CommCare Connect",
        max_length=100,
    )
