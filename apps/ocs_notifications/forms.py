from django import forms

from apps.ocs_notifications.models import LevelChoices, UserNotificationPreferences


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
