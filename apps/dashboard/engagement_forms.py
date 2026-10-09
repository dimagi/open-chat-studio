from typing import Any

from django import forms

from apps.annotations.models import Tag, TagCategories
from apps.channels.models import ChannelPlatform
from apps.experiments.models import Experiment, Participant


class EngagementFilterForm(forms.Form):
    experiments = forms.ModelMultipleChoiceField(
        queryset=Experiment.objects.none(), required=False, widget=forms.SelectMultiple()
    )
    channels = forms.MultipleChoiceField(choices=[], required=False, widget=forms.SelectMultiple())
    participants = forms.ModelMultipleChoiceField(
        queryset=Participant.objects.none(), required=False, widget=forms.SelectMultiple()
    )
    tags = forms.ModelMultipleChoiceField(queryset=Tag.objects.none(), required=False, widget=forms.SelectMultiple())

    def __init__(self, *args, team=None, **kwargs):
        super().__init__(*args, **kwargs)

        if not team:
            return

        self.fields["experiments"].queryset = Experiment.objects.filter(
            team=team, is_archived=False, working_version=None
        ).order_by("name")

        available_platform_labels = ChannelPlatform.for_filter(team)
        available_platform_labels.remove(ChannelPlatform.EVALUATIONS.label)
        label_to_value = {choice[1]: choice[0] for choice in ChannelPlatform.choices}
        self.fields["channels"].choices = [
            (label_to_value[label], label) for label in available_platform_labels if label in label_to_value
        ]

        self.fields["participants"].queryset = Participant.objects.select_related("user").filter(team=team)
        self.fields["tags"].queryset = Tag.objects.filter(team=team).exclude(category=TagCategories.EXPERIMENT_VERSION)

    def get_filter_params(self) -> dict[str, Any]:
        if not self.is_valid():
            return {}

        data = self.cleaned_data
        params = {}

        if data.get("experiments"):
            params["experiment_ids"] = list(data["experiments"].values_list("id", flat=True))
        if data.get("channels"):
            params["platform_names"] = data["channels"]
        if data.get("participants"):
            params["participant_ids"] = list(data["participants"].values_list("id", flat=True))
        if data.get("tags"):
            params["tag_ids"] = list(data["tags"].values_list("id", flat=True))

        return params
