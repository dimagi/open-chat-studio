from django import forms

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
        fields = ["messaging_provider", "channel_name", "level", "enabled"]
        widgets = {
            "level": forms.RadioSelect(choices=LevelChoices.choices),
        }
        help_texts = {
            "channel_name": "The Slack channel to post to, e.g. #alerts.",
            "level": "Only notifications at or above this level are posted.",
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["messaging_provider"].queryset = MessagingProvider.objects.filter(
            team=request.team, type=MessagingProviderType.slack
        )
