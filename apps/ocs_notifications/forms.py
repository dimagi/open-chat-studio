from django import forms

from apps.channels.slack_utils import normalize_slack_channel_name, resolve_slack_channel
from apps.ocs_notifications.models import LevelChoices, NotificationChannel, UserNotificationPreferences
from apps.service_providers.models import MessagingProvider, MessagingProviderType


class NotificationPreferencesForm(forms.ModelForm):
    class Meta:
        model = UserNotificationPreferences
        fields = [
            "in_app_enabled",
            "in_app_level",
            "email_enabled",
            "email_level",
        ]
        widgets = {
            "in_app_enabled": forms.CheckboxInput(),
            "email_enabled": forms.CheckboxInput(),
            "in_app_level": forms.RadioSelect(choices=LevelChoices.choices),
            "email_level": forms.RadioSelect(choices=LevelChoices.choices),
        }


class NotificationChannelForm(forms.ModelForm):
    messaging_provider = forms.ModelChoiceField(
        queryset=MessagingProvider.objects.none(),
        label="Slack workspace",
        help_text="The Slack messaging provider that posts notifications.",
    )

    class Meta:
        model = NotificationChannel
        fields = ["messaging_provider", "channel_name", "channel_id", "level", "enabled"]
        widgets = {
            "level": forms.RadioSelect(choices=LevelChoices.choices),
            "channel_id": forms.HiddenInput(),
        }
        help_texts = {
            "channel_name": "The Slack channel to post to, e.g. #alerts.",
            "level": "Only notifications at or above this level are posted.",
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.fields["messaging_provider"].queryset = MessagingProvider.objects.filter(
            team=request.team, type=MessagingProviderType.slack
        )
        if self.instance and not self.instance.team_id:
            self.instance.team_id = request.team.id

    def clean_channel_name(self):
        name = self.cleaned_data["channel_name"]
        return normalize_slack_channel_name(name)

    def clean(self):
        cleaned_data = super().clean()
        provider = cleaned_data.get("messaging_provider")
        level = cleaned_data.get("level")
        if provider is None or level is None:
            return cleaned_data
        channel_name = cleaned_data.get("channel_name")
        if channel_name:
            channel = resolve_slack_channel(provider, channel_name) or {}
            cleaned_data["channel_id"] = channel.get("id", channel_name)
        existing = NotificationChannel.objects.filter(team=self.request.team, messaging_provider=provider, level=level)
        if self.instance and self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            self.add_error(
                "messaging_provider",
                "This Slack workspace already has a notification channel at this level.",
            )
        return cleaned_data
