import json
import logging
from functools import cached_property

from django import forms
from django.db import transaction

from apps.channels.exceptions import ExperimentChannelException
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.utils import validate_platform_availability
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.teams.models import Team

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
            self.extra_form = self.channel.extra_form(experiment=experiment, data=data)
        else:
            initial = initial or {}
            initial["platform"] = self.platform.value

            self.channel_form = ChannelForm(experiment=self.experiment, data=data, initial=initial)
            self.extra_form = self.platform.extra_form(experiment=experiment, data=data)

    def is_valid(self):
        """Validate both forms"""
        channel_valid = self.channel_form.is_valid()
        extra_valid = self.extra_form.is_valid() if self.extra_form else True
        if channel_valid and extra_valid:
            if not self.channel:
                # skip platform validation when updating an existing channel
                self.validate_platform()

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

        with transaction.atomic():
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
        fields = ["name", "platform", "messaging_provider", "enabled", "disabled_message"]
        widgets = {
            "platform": forms.HiddenInput(),
            "disabled_message": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "e.g. This bot is temporarily unavailable. Please try again later.",
                    # Only relevant while the channel is off
                    "control_attrs": {"x-show": "!channelEnabled"},
                }
            ),
        }

    def __init__(self, experiment, *args, **kwargs):
        initial: dict = kwargs.get("initial", {})
        initial.setdefault("name", experiment.name)
        super().__init__(*args, **kwargs)
        platform = self.initial["platform"]
        self._populate_available_message_providers(experiment.team, platform)
        self.fields["enabled"].widget.attrs["x-model.boolean"] = "channelEnabled"
        self.form_attrs = {"x-data": json.dumps({"channelEnabled": self._enabled_initial()})}

    def _enabled_initial(self) -> bool:
        """The value the Alpine toggle starts on: the bound value if the form was submitted,
        otherwise the instance's (new channels default to enabled)."""
        if self.is_bound:
            return bool(self.data.get(self.add_prefix("enabled")))
        if self.instance.pk:
            return bool(self.instance.enabled)
        return True

    def _populate_available_message_providers(self, team: Team, platform: ChannelPlatform):
        provider_types = MessagingProviderType.platform_supported_provider_types(platform)
        queryset = MessagingProvider.objects.filter(team=team)
        # We must let the default queryset filter for the specific team
        self.fields["messaging_provider"].queryset = queryset
        if provider_types:
            self.fields["messaging_provider"].queryset = queryset.filter(type__in=provider_types)
        else:
            self.fields["messaging_provider"].widget = forms.HiddenInput()

    def save(self, experiment, config_data: dict):  # ty: ignore[invalid-method-override]
        self.instance.team = experiment.team
        self.instance.experiment = experiment
        self.instance.extra_data = config_data
        return super().save()


class ExtraFormBase(forms.Form):
    success_message = ""
    warning_message = ""
    form_attrs = {}
    """Additional HTML attributes to be added to the form element"""
    custom_template = None
    """Template to render the form's fields with, when the default field rendering won't do"""

    def __init__(self, experiment, channel=None, **kwargs):
        self.experiment = experiment
        self.channel = channel
        super().__init__(**kwargs)

    @cached_property
    def messaging_provider(self) -> MessagingProvider | None:
        """The submitted provider, scoped to the experiment's team."""
        if provider_id := self.data.get("messaging_provider"):
            return MessagingProvider.objects.filter(id=provider_id, team=self.experiment.team).first()
        return None

    def clean(self):
        if platform_slug := self.data.get("platform"):
            platform = ChannelPlatform(platform_slug)
            if platform.channel_identifier_key:
                channel_identifier = self.cleaned_data.get(platform.channel_identifier_key, "")
                try:
                    ExperimentChannel.check_usage_by_another_experiment(
                        platform,
                        identifier=channel_identifier,
                        new_experiment=self.experiment,
                    )
                except ChannelAlreadyUtilizedException as e:
                    field = platform.channel_identifier_key if platform.channel_identifier_key in self.fields else None
                    self.add_error(field, e.html_message)
        return self.cleaned_data

    def post_save(self, channel: ExperimentChannel):
        """Override this method to perform any additional actions after the channel has been saved"""
        pass

    def configure_webhook(self, channel: ExperimentChannel):
        """Point the channel's inbound webhook at us, via its WebhookManager.

        Falls back to manual setup instructions when the channel has no manager or the
        manager cannot configure webhooks. On failure, surfaces a warning rather than
        raising, so channel creation still succeeds.
        """
        try:
            manager = channel.get_webhook_manager()
            if not manager or not manager.supports_webhook_management:
                if channel.webhook_url:
                    self.success_message = f"Use the following URL when setting up the webhook: {channel.webhook_url}"
                return
            manager.set_incoming_webhook(channel.extra_data, channel.webhook_url)
        except Exception:
            logger.exception("Error configuring webhook for channel %s", channel.id)
            self.warning_message = (
                "Could not configure the webhook automatically. "
                f"Use the following URL when setting up the webhook: {channel.webhook_url}"
            )
        else:
            self.success_message = "Webhook configured automatically."


class WebhookUrlFormBase(ExtraFormBase):
    webook_url = forms.CharField(
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
        label="Webhook URL",
        disabled=True,
        required=False,
        help_text="Use this as the URL when setting up the webhook",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.channel:
            self.initial["webook_url"] = self.channel.webhook_url

        if not self.channel:
            # We only show the webhook URL field when there is something to show
            self.fields["webook_url"].widget = forms.HiddenInput()

    def post_save(self, channel: ExperimentChannel):
        self.success_message = f"Use the following URL when setting up the webhook: {channel.webhook_url}"
