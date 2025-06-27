from dateutil.relativedelta import relativedelta
from django import forms
from django.contrib.auth import get_user_model
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models import TextChoices
from django.utils import timezone

from apps.teams.models import Team

User = get_user_model()


class DateRanges(TextChoices):
    THIS_MONTH = "m0", "Month to date"
    LAST_MONTH = "m1", "Last month"
    LAST_30_DAYS = "d30", "Last 30 days"
    CUSTOM = "custom", "Custom"

    def get_date_range(self):
        now = timezone.now().date()
        match self:
            case DateRanges.THIS_MONTH:
                return now.replace(day=1), now
            case DateRanges.LAST_MONTH:
                start = now - relativedelta(months=1, day=1)
                end = start + relativedelta(day=31)  # gets coerced to the last day of the month
                return start, end
            case DateRanges.LAST_30_DAYS:
                return now - relativedelta(days=30), now
            case DateRanges.CUSTOM:
                return None


class DateRangeForm(forms.Form):
    range_type = forms.ChoiceField(label="Date Range", choices=DateRanges.choices, initial=DateRanges.LAST_30_DAYS)
    start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def get_date_range(self):
        range_type = self.cleaned_data["range_type"]
        if range_type == DateRanges.CUSTOM:
            return self.cleaned_data["start"], self.cleaned_data["end"]
        return DateRanges(range_type).get_date_range()


class FlagUpdateForm(forms.Form):
    everyone = forms.BooleanField(required=False)
    testing = forms.BooleanField(required=False)
    superusers = forms.BooleanField(required=False)
    rollout = forms.BooleanField(required=False)
    percent = forms.IntegerField(
        required=False,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Percentage for rollout (0-100)",
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Note where this Flag is used",
    )
    teams = forms.ModelMultipleChoiceField(
        queryset=Team.objects.all(), required=False, widget=forms.MultipleHiddenInput()
    )
    users = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(), required=False, widget=forms.MultipleHiddenInput()
    )

    def clean_percent(self):
        percent = self.cleaned_data.get("percent")
        rollout = self.cleaned_data.get("rollout")

        if rollout and percent is None:
            raise forms.ValidationError("Percentage is required when rollout is enabled")

        return percent
