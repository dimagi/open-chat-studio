from django import forms
from django.utils.translation import gettext_lazy as _

from apps.service_providers.models import ModerationProvider
from apps.teams.models import Team


class TeamModerationForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ("moderation_enabled", "moderation_provider", "auto_block_enabled")
        labels = {
            "moderation_enabled": _("Moderate participant messages"),
            "moderation_provider": _("Moderation provider"),
            "auto_block_enabled": _("Auto-block"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["moderation_provider"].queryset = ModerationProvider.objects.filter(team=self.instance).order_by(
            "name"
        )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("moderation_enabled") and not cleaned_data.get("moderation_provider"):
            self.add_error(field="moderation_provider", error=_("Select a moderation provider to turn moderation on."))
        return cleaned_data

    def save(self, commit=True):
        team = super().save(commit=False)
        if commit:
            team.save(update_fields=[*self.Meta.fields, "updated_at"])
            self.save_m2m()
        return team
