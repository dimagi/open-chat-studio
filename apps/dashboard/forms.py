from datetime import timedelta

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.annotations.models import Tag, TagCategories
from apps.channels.models import ChannelPlatform
from apps.experiments.models import Experiment
from apps.participants.models import Participant

# TODO: Update Participant import


class DashboardFilterForm(forms.Form):
    """Form for filtering dashboard data"""

    # Date range options
    DATE_RANGE_CHOICES = [
        ("7", _("Last 7 days")),
        ("30", _("Last 30 days")),
        ("90", _("Last 3 months")),
        ("365", _("Last year")),
        ("custom", _("Custom range")),
    ]

    # Time granularity options
    GRANULARITY_CHOICES = [
        ("hourly", _("Hourly")),
        ("daily", _("Daily")),
        ("weekly", _("Weekly")),
        ("monthly", _("Monthly")),
    ]

    date_range = forms.ChoiceField(
        choices=DATE_RANGE_CHOICES,
        initial="30",
        required=False,
        widget=forms.Select(attrs={"data-filter-type": "date_range"}),
    )

    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "data-filter-type": "start_date"}),
    )

    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "data-filter-type": "end_date"}),
    )

    granularity = forms.ChoiceField(
        choices=GRANULARITY_CHOICES,
        initial="daily",
        required=False,
        widget=forms.Select(attrs={"data-filter-type": "granularity"}),
    )

    experiments = forms.ModelMultipleChoiceField(
        queryset=Experiment.objects.none(),
        required=False,
        widget=forms.SelectMultiple(),
    )

    channels = forms.MultipleChoiceField(
        choices=[],  # Will be set dynamically in __init__
        required=False,
        widget=forms.SelectMultiple(),
    )

    participants = forms.ModelMultipleChoiceField(
        queryset=Participant.objects.none(), required=False, widget=forms.SelectMultiple()
    )

    tags = forms.ModelMultipleChoiceField(queryset=Tag.objects.none(), required=False, widget=forms.SelectMultiple())

    def __init__(self, *args, team=None, **kwargs):
        super().__init__(*args, **kwargs)

        if team:
            # Filter experiments and channels by team
            self.fields["experiments"].queryset = Experiment.objects.filter(
                team=team, is_archived=False, working_version=None
            ).order_by("name")

            # Set channel choices using ChannelPlatform.for_filter
            available_platform_labels = ChannelPlatform.for_filter(team)
            available_platform_labels.remove(ChannelPlatform.EVALUATIONS.label)

            # Create a mapping from label to value for available platforms
            label_to_value = {choice[1]: choice[0] for choice in ChannelPlatform.choices}
            # Build choices list using only available platforms
            platform_choices = [
                (label_to_value[label], label) for label in available_platform_labels if label in label_to_value
            ]
            self.fields["channels"].choices = platform_choices
            self.fields["participants"].queryset = Participant.objects.select_related("user").filter(team=team)
            self.fields["tags"].queryset = Tag.objects.filter(team=team).exclude(
                category=TagCategories.EXPERIMENT_VERSION
            )

        # Set default dates if not provided
        if not self.data.get("start_date") and not self.data.get("end_date"):
            today = timezone.now().date()
            if self.data.get("date_range") == "7":
                self.fields["start_date"].initial = today - timedelta(days=7)
                self.fields["end_date"].initial = today
            elif self.data.get("date_range") == "90":
                self.fields["start_date"].initial = today - timedelta(days=90)
                self.fields["end_date"].initial = today
            elif self.data.get("date_range") == "365":
                self.fields["start_date"].initial = today - timedelta(days=365)
                self.fields["end_date"].initial = today
            else:  # Default 30 days
                self.fields["start_date"].initial = today - timedelta(days=30)
                self.fields["end_date"].initial = today

    def clean(self):
        cleaned_data = super().clean()
        date_range = cleaned_data.get("date_range")
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        # Handle date range logic
        if date_range and date_range != "custom":
            today = timezone.now().date()
            days = int(date_range)
            cleaned_data["start_date"] = today - timedelta(days=days)
            cleaned_data["end_date"] = today
        elif date_range == "custom":
            if not start_date or not end_date:
                raise forms.ValidationError(_("Start date and end date are required for custom range."))
            if start_date > end_date:
                raise forms.ValidationError(_("Start date must be before end date."))

        return cleaned_data

    def get_filter_params(self):
        """Get cleaned filter parameters for the service layer"""
        if not self.is_valid():
            return {}

        data = self.cleaned_data
        params = {}

        if data.get("start_date"):
            # Convert to datetime for filtering
            params["start_date"] = timezone.make_aware(
                timezone.datetime.combine(data["start_date"], timezone.datetime.min.time())
            )

        if data.get("end_date"):
            # Convert to datetime for filtering (end of day)
            params["end_date"] = timezone.make_aware(
                timezone.datetime.combine(data["end_date"], timezone.datetime.max.time())
            )

        if data.get("experiments"):
            params["experiment_ids"] = list(data["experiments"].values_list("id", flat=True))

        if data.get("channels"):
            params["platform_names"] = data["channels"]

        if data.get("participants"):
            params["participant_ids"] = list(data["participants"].values_list("id", flat=True))

        if data.get("tags"):
            params["tag_ids"] = list(data["tags"].values_list("id", flat=True))

        return params


class SavedFilterForm(forms.Form):
    """Form for saving/loading filter presets"""

    name = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": _("Filter preset name")}),
    )

    is_default = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))

    filter_data = forms.CharField(widget=forms.HiddenInput(), required=True)
