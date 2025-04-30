import csv
import io

from django import forms

from apps.experiments.models import ExperimentSession
from apps.service_providers.models import LlmProvider, LlmProviderModel

from .models import AnalysisQuery, TranscriptAnalysis


class TranscriptAnalysisForm(forms.ModelForm):
    query_file = forms.FileField(
        help_text="Upload a CSV file with query prompts. "
        "Each row should contain Query Name, Query Description, and optionally Output Format."
    )

    class Meta:
        model = TranscriptAnalysis
        fields = ["name", "description", "query_file"]

    def __init__(self, *args, **kwargs):
        self.experiment_id = kwargs.pop("experiment_id", None)
        self.team = kwargs.pop("team", None)
        super().__init__(*args, **kwargs)

        if self.experiment_id and self.team:
            # Get available sessions for the given experiment
            sessions = ExperimentSession.objects.filter(
                experiment_id=self.experiment_id, team=self.team
            ).select_related("participant")

            # Add multiple choice field for sessions
            self.fields["sessions"] = forms.ModelMultipleChoiceField(
                queryset=sessions,
                widget=forms.CheckboxSelectMultiple,
                required=True,
                label="Select Sessions to Analyze",
            )

            # Set up LLM provider model field
            llm_providers = LlmProvider.objects.filter(team=self.team).all()
            llm_provider_models_by_type = {}
            for model in LlmProviderModel.objects.for_team(self.team):
                llm_provider_models_by_type.setdefault(model.type, []).append(model)
            model_choices = []
            for provider in llm_providers:
                for model in llm_provider_models_by_type.get(provider.type, []):
                    print((f"{provider.id}:{model.id}", f"{provider.name} - {model!s}"))
                    model_choices.append((f"{provider.id}:{model.id}", f"{provider.name} - {model!s}"))

            self.fields["provider_model"] = forms.ChoiceField(
                choices=model_choices,
                required=True,
                label="Select LLM Provider Model",
                help_text="Choose the LLM model to use for analyzing transcripts.",
            )

    def clean_query_file(self):
        query_file = self.cleaned_data.get("query_file")
        if not query_file:
            return None

        # Check file extension
        if not query_file.name.endswith(".csv"):
            raise forms.ValidationError("Only CSV files are allowed.")

        # Basic validation of CSV format
        try:
            decoded_file = query_file.read().decode("utf-8")
            io_string = io.StringIO(decoded_file)
            reader = csv.reader(io_string)
            rows = list(reader)

            if not rows:
                raise forms.ValidationError("The CSV file is empty.")

            # Reset file pointer for later use
            query_file.seek(0)

        except Exception as e:
            raise forms.ValidationError(f"Error reading CSV file: {str(e)}") from e

        return query_file

    def clean_llm_provider_model(self):
        provider_model = self.cleaned_data.get("provider_model")
        if not provider_model:
            raise forms.ValidationError("Please select a valid LLM provider model.")

        # Validate the selected model
        try:
            provider_id, model_id = map(int, provider_model.split(":"))
            if not LlmProviderModel.objects.filter(team=self.team, id=model_id).exists():
                raise forms.ValidationError("Invalid LLM provider model selected.")
            if not LlmProvider.objects.filter(team=self.team, id=provider_id).exists():
                raise forms.ValidationError("Invalid LLM provider selected.")
        except ValueError:
            raise forms.ValidationError("Invalid selection for LLM provider model.") from None

        return provider_model

    def save(self, commit=True):
        instance = super().save(commit=False)
        provider_id, model_id = map(int, self.cleaned_data["provider_model"].split(":"))
        instance.llm_provider_id = provider_id
        instance.llm_provider_model_id = model_id

        if commit:
            instance.save()

            # Handle sessions
            if "sessions" in self.cleaned_data:
                instance.sessions.set(self.cleaned_data["sessions"])

            # Process query file
            query_file = self.cleaned_data.get("query_file")
            if query_file:
                self._process_query_file(instance, query_file)

        return instance

    def _process_query_file(self, analysis, query_file):
        """Process the CSV query file and create AnalysisQuery objects"""
        try:
            decoded_file = query_file.read().decode("utf-8")
            io_string = io.StringIO(decoded_file)
            reader = csv.reader(io_string)

            for i, row in enumerate(reader):
                if not row or (len(row) == 1 and not row[0].strip()):
                    continue  # Skip empty rows

                name = row[0] if len(row) > 0 else ""
                prompt = row[1] if len(row) > 1 else name  # If only one column, use it as both name and prompt
                output_format = row[2] if len(row) > 2 else ""

                AnalysisQuery.objects.create(
                    analysis=analysis, name=name, prompt=prompt, output_format=output_format, order=i
                )
        except Exception as e:
            # Log the error but don't stop the process
            # In a real app, we might want to delete the analysis or mark it as failed
            print(f"Error processing query file: {str(e)}")
