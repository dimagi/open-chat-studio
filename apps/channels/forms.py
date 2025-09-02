import logging
import re
from functools import cached_property

import phonenumbers
from django import forms
from django.conf import settings
from django.urls import reverse
from django.utils.crypto import get_random_string
from telebot import TeleBot, apihelper, types

from apps.channels.const import SLACK_ALL_CHANNELS
from apps.channels.exceptions import ExperimentChannelException
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.teams.models import Team
from apps.web.meta import absolute_url

logger = logging.getLogger("ocs.channels")


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
        if self.is_bound:
            return
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


class EmbeddedWidgetChannelForm(ExtraFormBase):
    widget_token = forms.CharField(
        label="Widget Token",
        max_length=32,
        disabled=True,
        required=False,
        help_text="Auto-generated secure token for widget authentication.",
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
    )

    allowed_domains = forms.CharField(
        label="Allowed Domains",
        widget=forms.Textarea(
            attrs={"rows": 5, "placeholder": "example.com\n*.subdomain.com\nlocalhost:3000\n127.0.0.1:8000"}
        ),
        help_text=(
            "Enter allowed domains one per line. Supports:<br>"
            "• Exact domains: <code>example.com</code><br>"
            "• Wildcard subdomains: <code>*.example.com</code><br>"
            "• With ports: <code>localhost:3000</code>"
        ),
        required=True,
    )

    def __init__(self, *args, **kwargs):
        channel: ExperimentChannel = kwargs.pop("channel", None)

        # Set up initial data properly
        initial = kwargs.get("initial", {})

        # If editing existing channel, populate with existing data
        if channel and channel.extra_data.get("widget_token"):
            initial["widget_token"] = channel.extra_data.get("widget_token")
            initial["allowed_domains"] = "\n".join(channel.extra_data.get("allowed_domains", []))
        # If creating new channel, generate token with get_random_string(32)
        elif not initial.get("widget_token"):
            initial["widget_token"] = get_random_string(32)  # TOKEN AUTO-GENERATION

        kwargs["initial"] = initial
        super().__init__(*args, **kwargs)

    def clean_allowed_domains(self):
        domains_text = self.cleaned_data.get("allowed_domains", "")
        domains = [domain.strip() for domain in domains_text.split("\n") if domain.strip()]

        if not domains:
            raise forms.ValidationError("At least one domain must be specified.")

        validated_domains = []
        for domain in domains:
            if self._is_valid_domain_pattern(domain):
                validated_domains.append(domain)
            else:
                raise forms.ValidationError(f"Invalid domain format: '{domain}'")

        return validated_domains

    def _is_valid_domain_pattern(self, domain: str) -> bool:
        dev_patterns = ["localhost", "127.0.0.1", "0.0.0.0", "::1"]
        if any(domain.startswith(pattern) for pattern in dev_patterns):
            return True

        if domain.startswith("*."):
            domain = domain[2:]
        domain_without_port = domain.split(":")[0]
        domain_regex = re.compile(
            r"^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$"
        )

        return bool(domain_regex.match(domain_without_port))

    def post_save(self, channel: ExperimentChannel):
        token = channel.extra_data.get("widget_token")
        domains = channel.extra_data.get("allowed_domains", [])
        domains_text = ", ".join(domains[:3])  # Show first 3 domains
        if len(domains) > 3:
            domains_text += f" (and {len(domains) - 3} more)"

        self.success_message = (
            f"Embedded widget channel created successfully!<br><br>"
            f"<strong>Token:</strong> <code>{token}</code><br>"
            f"<strong>Allowed domains:</strong> {domains_text}<br><br>"
            f"Use this token in your widget's x-embed-key header."
        )


# utils -- TODO: move to a new utils.py
def validate_embedded_widget_request(token: str, origin_domain: str, team) -> tuple[bool, ExperimentChannel]:
    if not token or not origin_domain:
        return False, None

    try:
        channel = ExperimentChannel.objects.get(
            team=team, platform=ChannelPlatform.EMBEDDED_WIDGET, extra_data__widget_token=token, deleted=False
        )

        allowed_domains = channel.extra_data.get("allowed_domains", [])

        for allowed_domain in allowed_domains:
            if match_domain_pattern(origin_domain, allowed_domain):
                return True, channel

        return False, None

    except ExperimentChannel.DoesNotExist:
        return False, None


def match_domain_pattern(origin_domain: str, allowed_pattern: str) -> bool:
    """
    Check if origin domain matches the allowed domain pattern.
    """
    if origin_domain == allowed_pattern:
        return True

    origin_parts = origin_domain.split(":")
    pattern_parts = allowed_pattern.split(":")

    origin_domain_part = origin_parts[0]
    origin_port = origin_parts[1] if len(origin_parts) > 1 else None

    pattern_domain_part = pattern_parts[0]
    pattern_port = pattern_parts[1] if len(pattern_parts) > 1 else None

    # If pattern specifies a port, origin must match that port exactly
    if pattern_port is not None:
        if origin_port != pattern_port:
            return False

    if origin_domain_part == pattern_domain_part:
        return True

    if pattern_domain_part.startswith("*."):
        base_domain = pattern_domain_part[2:]  # Remove "*."
        if origin_domain_part.endswith("." + base_domain):
            return True

    return False


def extract_domain_from_headers(request) -> str:
    origin = request.headers.get("Origin")
    if origin:
        return origin.replace("http://", "").replace("https://", "")

    referer = request.headers.get("Referer")
    if referer:
        domain = referer.replace("http://", "").replace("https://", "").split("/")[0]
        return domain

    return ""
