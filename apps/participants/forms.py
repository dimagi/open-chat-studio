from django import forms

from apps.experiments.models import Experiment, Participant


class ParticipantForm(forms.ModelForm):
    identifier = forms.CharField(disabled=True)
    public_id = forms.CharField(disabled=True)

    class Meta:
        model = Participant
        fields = ("identifier", "public_id", "user")


class ParticipantImportForm(forms.Form):
    file = forms.FileField(
        help_text="CSV file with participant data. "
        "Supported columns: identifier, platform, name, data.* (for custom data fields)."
    )
    experiment = forms.ModelChoiceField(
        label="Chatbot",
        queryset=Experiment.objects.none(),
        help_text="Select the chatbot to associate the data with. "
        "This is only required if your CSV file contains 'data.*' fields.",
        required=False,
    )

    def __init__(self, *args, **kwargs):
        team = kwargs.pop("team", None)
        super().__init__(*args, **kwargs)
        if team:
            self.fields["experiment"].queryset = Experiment.objects.filter(team=team, working_version__isnull=True)

    def clean(self):
        if not self.cleaned_data.get("experiment"):
            # validate that the file doesn't contain any data.* columns
            pass
