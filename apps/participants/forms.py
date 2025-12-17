import json
import logging

from django import forms
from django.db.models import OuterRef, Subquery

from apps.channels.models import ExperimentChannel
from apps.experiments.models import Experiment, Participant

logger = logging.getLogger("ocs.participants")


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
        cleaned_data = super().clean()
        file = cleaned_data.get("file")
        experiment = cleaned_data.get("experiment")

        if file:
            # Check if CSV contains data.* columns
            import csv
            import io

            file.seek(0)
            try:
                content = file.read().decode("utf-8")
                csv_reader = csv.DictReader(io.StringIO(content))
                headers = csv_reader.fieldnames or []
            except UnicodeDecodeError:
                raise forms.ValidationError("File must be a valid CSV with UTF-8 encoding.") from None
            except Exception as e:
                logger.exception("error importing file: %s", e)
                raise forms.ValidationError("Error reading CSV file.") from e
            finally:
                file.seek(0)  # Reset file pointer for later use

            # Check if any headers start with 'data.'
            has_data_columns = any(header.startswith("data.") for header in headers)

            if has_data_columns and not experiment:
                raise forms.ValidationError("An chatbot must be selected when importing files with 'data.*' columns.")

        return cleaned_data


class ParticipantExportForm(forms.Form):
    experiment = forms.ModelChoiceField(
        label="Chatbot",
        queryset=Experiment.objects.none(),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        team = kwargs.pop("team", None)
        super().__init__(*args, **kwargs)
        if team:
            self.fields["experiment"].queryset = Experiment.objects.filter(team=team, working_version__isnull=True)


class TriggerBotForm(forms.Form):
    prompt_text = forms.CharField(
        label="Prompt Text",
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="The prompt to send to the bot",
        required=True,
    )
    experiment = forms.ModelChoiceField(
        label="Select Chatbot",
        queryset=Experiment.objects.none(),
        required=True,
        help_text="Select the chatbot to trigger",
    )
    start_new_session = forms.BooleanField(
        label="Start a new session",
        required=False,
        initial=False,
        help_text="End any previous sessions and start a new one",
    )
    session_data = forms.CharField(
        label="Session Data (JSON)",
        widget=forms.HiddenInput(),
        required=False,
        initial="{}",
    )

    def __init__(self, *args, **kwargs):
        participant = kwargs.pop("participant", None)
        team = participant.team
        super().__init__(*args, **kwargs)
        if team and participant:
            # Filter experiments to those that have a channel matching the participant's platform
            # This excludes the web channel, since we can't trigger bots on web participants
            experiment_ids = ExperimentChannel.objects.filter(
                team=team, platform=participant.platform, experiment_id=OuterRef("pk")
            ).values_list("experiment_id", flat=True)
            self.fields["experiment"].queryset = Experiment.objects.filter(
                team=team, is_version=False, id__in=Subquery(experiment_ids)
            )

    def clean_session_data(self):
        session_data = self.cleaned_data.get("session_data", "")
        if session_data:
            try:
                data = json.loads(session_data)
                if not isinstance(data, dict):
                    raise forms.ValidationError("Session data must be a valid JSON object")
                return data
            except json.JSONDecodeError as e:
                raise forms.ValidationError(f"Invalid JSON: {e}") from None
        return {}
