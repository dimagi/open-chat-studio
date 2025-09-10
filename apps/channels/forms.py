import json
import logging
import re
from functools import cached_property

import phonenumbers
from django import forms
from django.conf import settings
from django.urls import reverse
from telebot import TeleBot, apihelper, types

from apps.channels.const import SLACK_ALL_CHANNELS
from apps.channels.exceptions import ExperimentChannelException
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.utils import validate_platform_availability
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.teams.models import Team
from apps.web.meta import absolute_url

logger = logging.getLogger("ocs.channels")


class ChannelFormWrapper:
    """
    A wrapper class that combines ChannelForm and platform-specific extra forms
    to work with Django's built-in CreateView and UpdateView.
    """

    def __init__(
        self, experiment, platform, channel=None, data: dict | None = None, initial: dict | None = None, **kwargs
    ):
        self.experiment = experiment
        self.platform = platform
        self.channel = channel

        if self.channel:
            self.channel_form = ChannelForm(instance=channel, experiment=experiment, data=data)
            self.extra_form = self.channel.extra_form(data=data)
        else:
            initial = initial or {}
            initial["platform"] = self.platform.value

            self.channel_form = ChannelForm(experiment=self.experiment, data=data, initial=initial)
            self.extra_form = self.platform.extra_form(data=data)

    def is_valid(self):
        """Validate both forms"""
        channel_valid = self.channel_form.is_valid()
        extra_valid = self.extra_form.is_valid() if self.extra_form else True
        if channel_valid and extra_valid:
            if not self.channel:
                # skip platform validation when updating an existing channel
                self.validate_platform()
            try:
                self.extra_form.validate_channel_config(self.experiment, self.channel_form.cleaned_data["platform"])
            except ChannelAlreadyUtilizedException as e:
                self.channel_form.add_error(None, e.html_message)

            channel_valid = not self.channel_form.errors

        return channel_valid and extra_valid

    def validate_platform(self):
        try:
            validate_platform_availability(self.experiment, self.platform)
        except ExperimentChannelException as e:
            self.channel_form.add_error(None, str(e))

    def save(self, commit=True):
        """Save both forms"""
        config_data = {}
        if self.extra_form and self.extra_form.is_valid():
            config_data = self.extra_form.cleaned_data

        instance = self.channel_form.save(self.experiment, config_data)

        if self.extra_form and hasattr(self.extra_form, "post_save"):
            self.extra_form.post_save(channel=instance)

        return instance

    @property
    def success_message(self):
        return getattr(self.extra_form, "success_message", "")

    @property
    def warning_message(self):
        return getattr(self.extra_form, "warning_message", "")


class ChannelForm(forms.ModelForm):
    name = forms.CharField(required=False, help_text="If you leave this blank, it will default to the experiment name")

    class Meta:
        model = ExperimentChannel
        fields = ["name", "platform", "messaging_provider"]
        widgets = {"platform": forms.HiddenInput()}

    def __init__(self, experiment, *args, **kwargs):
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
        return None

    def validate_channel_config(self, experiment, platform_slug: str):
        platform = ChannelPlatform(platform_slug)
        channel_identifier = self.cleaned_data.get(platform.channel_identifier_key, "")

        ExperimentChannel.check_usage_by_another_experiment(
            platform,
            identifier=channel_identifier,
            new_experiment=experiment,
            messaging_provider=self.messaging_provider,
        )

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
    """Slack messaging channels can be configured as follows (in increasing order of specificity):
    * scope: all, is_default: True, keywords: []
        * Will be the fallback handler if no other channels match. There can only be one per Slack workspace
    * scope: all, is_default: False, keywords: [...]
        * Will match messages from any channel based on the keywords. Keywords must be unique.
    * scope: <channel>, is_default: False, keywords: []
        * Will match all messages on the given channel, regardless of keywords.

    This mode is not currently supported:
    * scope: <channel>, is_default: False, keywords: [...]
    """

    channel_scope = forms.ChoiceField(
        label="Where should this bot operate?",
        choices=[
            ("specific", "Specific channel"),
            ("all", "All channels"),
        ],
        widget=forms.RadioSelect(attrs={"x-model": "channelScope"}),
    )
    routing_method = forms.ChoiceField(
        label="How should this bot receive messages?",
        choices=[
            ("keywords", "Respond to specific keywords"),
            ("default", "Default fallback (no matched keywords)"),
        ],
        widget=forms.RadioSelect(
            attrs={"x-model": "routingMethod", "control_attrs": {"x-show": "channelScope === 'all'"}}
        ),
        required=False,
    )
    slack_channel_name = forms.CharField(
        label="Channel Name",
        max_length=100,
        widget=forms.TextInput(attrs={"control_attrs": {"x-show": "channelScope === 'specific'"}}),
        required=False,
        help_text="Enter the channel name (e.g., general, support)",
    )
    slack_channel_id = forms.CharField(widget=forms.HiddenInput(), required=False)
    keywords = forms.CharField(
        label="Keywords",
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "control_attrs": {"x-show": "routingMethod === 'keywords'"},
                "placeholder": "health, benefits, hr-support (comma-separated)",
            }
        ),
        required=False,
        help_text=(
            "Comma-separated keywords that will route messages to this bot when used as the first word after "
            "@mention (max 5 keywords, 25 chars each). Only letters, numbers, and hyphens allowed. "
            "Example: health, benefits, hr-support"
        ),
    )

    def __init__(self, *args, **kwargs):
        # Handle channel parameter for editing existing channels
        channel = kwargs.pop("channel", None)
        initial = kwargs.setdefault("initial", {})
        if channel:
            self.instance = channel
            # Merge stored config to drive scope/routing init logic and field values
            initial.update(channel.extra_data or {})

        # Set channel scope based on existing data
        if initial.get("slack_channel_id") == SLACK_ALL_CHANNELS:
            initial["channel_scope"] = "all"
            # Set routing method for "all channels"
            if initial.get("is_default"):
                initial["routing_method"] = "default"
            elif initial.get("keywords"):
                initial["routing_method"] = "keywords"
            else:
                initial["routing_method"] = "default"
        else:
            initial["channel_scope"] = "specific"
            # routing_method not needed for specific channels

        # Set keywords field from extra_data
        if "keywords" in initial and isinstance(initial["keywords"], list):
            initial["keywords"] = ", ".join(initial["keywords"])

        self.form_attrs = {
            "x-data": json.dumps(
                {
                    "channelScope": initial.get("channel_scope", "specific"),
                    "routingMethod": initial.get("routing_method", "default"),
                }
            )
        }
        super().__init__(*args, **kwargs)

    def clean_slack_channel_name(self):
        name = self.cleaned_data["slack_channel_name"].strip()
        if name.startswith("#"):
            name = name[1:]
        return name

    def clean_keywords(self):
        keywords_str = self.cleaned_data.get("keywords", "").strip()
        if not keywords_str:
            return []

        # Parse comma-separated keywords and clean them
        keywords = [kw.strip().lower() for kw in keywords_str.split(",") if kw.strip()]

        # Validate keyword count
        if len(keywords) > 5:
            raise forms.ValidationError("Too many keywords (maximum 5 allowed)")

        # Validate and sanitize each keyword
        sanitized_keywords = []
        for kw in keywords:
            # Check length
            if len(kw) > 25:
                raise forms.ValidationError(f"Keyword '{kw}' is too long (maximum 25 characters)")

            # Check for empty keywords after cleaning
            if not kw:
                raise forms.ValidationError("Keywords cannot be empty")

            # Sanitize: allow only alphanumeric and hyphens (no spaces for single-word matching)
            if not re.match(r"^[a-zA-Z0-9\-]+$", kw):
                raise forms.ValidationError(
                    f"Keyword '{kw}' contains invalid characters. Only letters, numbers, and hyphens are allowed."
                )

            sanitized_keywords.append(kw)

        # Remove duplicates while preserving order
        seen = set()
        unique_keywords = []
        for kw in sanitized_keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)
        return unique_keywords

    def clean(self):
        cleaned_data = super().clean()
        channel_scope = cleaned_data.get("channel_scope")
        routing_method = cleaned_data.get("routing_method")

        if not self.messaging_provider:
            raise forms.ValidationError("Messaging provider is required.")

        if channel_scope == "specific":
            channel_name = cleaned_data.get("slack_channel_name", "").strip()
            if not channel_name:
                raise forms.ValidationError("Channel name is required for specific channels.")

            service = self.messaging_provider.get_messaging_service()
            channel = service.get_channel_by_name(channel_name)
            if not channel:
                raise forms.ValidationError(f"No channel found with name {channel_name}")
            cleaned_data["slack_channel_id"] = channel["id"]

            # Specific channels don't use keywords or default routing
            cleaned_data["keywords"] = []
            cleaned_data["is_default"] = False
            self._validate_unique_channel(channel_name, channel["id"])

        elif channel_scope == "all":
            # All channels - set up based on routing method
            cleaned_data["slack_channel_id"] = SLACK_ALL_CHANNELS
            cleaned_data["slack_channel_name"] = SLACK_ALL_CHANNELS

            if routing_method == "keywords":
                keywords = cleaned_data.get("keywords", [])
                if not keywords:
                    raise forms.ValidationError("Keywords are required when using keyword routing.")

                # Check for duplicate keywords across other channels
                self._validate_unique_keywords(keywords)
                cleaned_data["is_default"] = False

            elif routing_method == "default":
                # Check for duplicate default bot
                self._validate_unique_default()
                cleaned_data["keywords"] = []
                cleaned_data["is_default"] = True
            else:
                raise forms.ValidationError("Select a routing method for 'All channels' (keywords or default).")

        return cleaned_data

    def _validate_unique_channel(self, slack_channel_name, slack_channel_id):
        queryset = self._get_channel_queryset().select_related("experiment").only("experiment__name")
        if existing_channel := queryset.filter(extra_data__slack_channel_id=slack_channel_id).first():
            # TODO: only show name if same team
            experiment_name = existing_channel.experiment.name
            raise forms.ValidationError(f"This channel is already being used by the '{experiment_name}' chatbot.")

    def _validate_unique_keywords(self, keywords):
        """Check that keywords are not already used by other channels system-wide"""
        # Normalize input keywords to lowercase for case-insensitive comparison
        keywords = [kw.lower() for kw in keywords]

        # Keywords must be unique across the entire Slack workspace
        queryset = self._get_channel_queryset().filter(
            extra_data__is_default=False,
            extra_data__slack_channel_id=SLACK_ALL_CHANNELS,
        )

        # Check each existing channel's keywords
        for channel in queryset:
            existing_keywords = [kw.lower() for kw in channel.extra_data.get("keywords", [])]
            if existing_keywords:
                conflicts = set(keywords) & set(existing_keywords)
                if conflicts:
                    conflict_list = ", ".join(sorted(conflicts))
                    raise forms.ValidationError(f"Keywords already in use by '{channel.name}': {conflict_list}")

    def _validate_unique_default(self):
        """Check that there isn't already a default bot for this messaging provider"""
        # Default bots must be unique across the entire Slack workspace
        queryset = self._get_channel_queryset().filter(
            extra_data__is_default=True, extra_data__slack_channel_id=SLACK_ALL_CHANNELS
        )
        if existing_default := queryset.first():
            raise forms.ValidationError(
                f"There is already a default bot: '{existing_default.name}'. "
                f"Please remove the default setting from that bot first."
            )

    def _get_current_channel_id(self):
        """Get current channel ID, with fallback logic for missing instance"""
        # Try instance first
        if (
            hasattr(self, "instance")
            and self.instance
            and hasattr(self.instance, "pk")
            and self.instance.pk is not None
        ):
            return self.instance.pk

        return None

    def _get_channel_queryset(self):
        queryset = ExperimentChannel.objects.filter(
            platform=ChannelPlatform.SLACK,
            deleted=False,
            # scope to providers connected to the same slack workspace
            messaging_provider__config__slack_team_id=self.messaging_provider.config.get("slack_team_id"),
        )
        if current_channel_id := self._get_current_channel_id():
            queryset = queryset.exclude(pk=current_channel_id)
        return queryset

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
