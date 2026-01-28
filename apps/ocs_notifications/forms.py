from django import forms

from apps.ocs_notifications.models import UserNotificationPreferences


class NotificationPreferencesForm(forms.ModelForm):
    in_app_enabled = forms.BooleanField(required=False, widget=forms.CheckboxInput())
    in_app_level = forms.RadioSelect()
    email_enabled = forms.BooleanField(required=False, widget=forms.CheckboxInput())
    email_level = forms.RadioSelect()

    class Meta:
        model = UserNotificationPreferences
        fields = [
            "in_app_enabled",
            "in_app_level",
            "email_enabled",
            "email_level",
        ]
