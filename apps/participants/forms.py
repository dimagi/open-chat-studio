import logging

from django import forms

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
