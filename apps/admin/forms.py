from dateutil.relativedelta import relativedelta
from django import forms
from django.db.models import TextChoices
from django.utils import timezone


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
                return now - relativedelta(months=1, day=1), now + relativedelta(day=31)
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
