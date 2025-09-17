import csv
import io
from urllib.parse import urlparse

from django import forms
from django.conf import settings
from django.http import QueryDict
from django.urls import reverse
from django.utils.html import format_html

from apps.service_providers.models import LlmProvider, LlmProviderModel

from ..experiments.export import get_filtered_sessions
from ..service_providers.utils import get_dropdown_llm_model_choices
from .const import LANGUAGE_CHOICES
from .models import AnalysisQuery, TranscriptAnalysis


class TranscriptAnalysisForm(forms.ModelForm):
    query_file = forms.FileField(
        required=False,
        help_text="Upload a CSV file with query prompts. "
        "Each row should contain Query Name, Query Description, and optionally Output Format.",
    )

    class Meta:
        model = TranscriptAnalysis
        fields = ["name", "description", "query_file"]

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        self.experiment = kwargs.pop("experiment", None)
        self.team = kwargs.pop("team", None)
        super().__init__(*args, **kwargs)

        timezone = self.request.session.get("detected_tz", None)
        referer = self.request.headers.get("referer") or ""
        parsed_url = urlparse(referer)
        query_params = QueryDict(parsed_url.query)
        sessions = get_filtered_sessions(self.experiment, query_params, timezone)
        session_ids = sessions.values_list("id", flat=True)[: settings.ANALYTICS_MAX_SESSIONS]

        self.fields["sessions"] = SessionChoiceField(
            queryset=sessions.select_related("experiment", "team"),
            widget=forms.CheckboxSelectMultiple(attrs={"class": "session-checkbox", "@click": "checkLimits()"}),
            required=True,
            label="",
            initial=session_ids,
        )

        # Set up LLM provider model field
        model_choices = get_dropdown_llm_model_choices(self.team)

        self.fields["provider_model"] = forms.ChoiceField(
            choices=model_choices,
            required=True,
            label="Select LLM Provider Model",
            help_text="Choose the LLM model to use for analyzing transcripts.",
        )
        self.fields["translation_language"] = forms.ChoiceField(
            choices=LANGUAGE_CHOICES,
            required=False,
            label="Translation Language",
            help_text="Select language to translate transcripts to before analysis",
        )

        self.fields["translation_provider_model"] = forms.ChoiceField(
            choices=model_choices,
            required=False,
            label="Translation LLM Provider Model",
            help_text="Choose the LLM model to use for translating transcripts.",
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

    def _validate_provider_model(self, provider_model_value, llm_field_type="LLM"):
        """Shared validation logic for provider model fields."""
        try:
            provider_id, model_id = map(int, provider_model_value.split(":"))
            if not LlmProviderModel.objects.for_team(self.team).filter(id=model_id).exists():
                raise forms.ValidationError(f"Invalid {llm_field_type} provider model selected.")
            if not LlmProvider.objects.filter(team=self.team, id=provider_id).exists():
                raise forms.ValidationError(f"Invalid {llm_field_type} provider selected.")
        except ValueError:
            raise forms.ValidationError(f"Invalid selection for {llm_field_type} provider model.") from None

        return provider_model_value

    def clean_provider_model(self):
        provider_model = self.cleaned_data.get("provider_model")
        if not provider_model:
            raise forms.ValidationError("Please select a valid LLM provider model.")

        return self._validate_provider_model(provider_model, "LLM")

    def clean_translation_provider_model(self):
        translation_provider_model = self.cleaned_data.get("translation_provider_model")

        if translation_provider_model:
            return self._validate_provider_model(translation_provider_model, "translation LLM")

        return translation_provider_model

    def clean(self):
        cleaned_data = super().clean()
        translation_language = cleaned_data.get("translation_language")
        translation_provider_model = cleaned_data.get("translation_provider_model")
        if translation_language and not translation_provider_model:
            raise forms.ValidationError(
                {
                    "translation_provider_model": "Translation LLM provider is required when translation \
                    language is selected."
                }
            )

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        provider_id, model_id = map(int, self.cleaned_data["provider_model"].split(":"))
        instance.llm_provider_id = provider_id
        instance.llm_provider_model_id = model_id
        instance.translation_language = self.cleaned_data.get("translation_language")

        translation_provider_model = self.cleaned_data.get("translation_provider_model")
        if translation_provider_model:
            translation_provider_id, translation_model_id = map(int, translation_provider_model.split(":"))
            instance.translation_llm_provider_id = translation_provider_id
            instance.translation_llm_provider_model_id = translation_model_id

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
        query_file.seek(0)
        decoded_file = query_file.read().decode("utf-8")
        io_string = io.StringIO(decoded_file)
        reader = csv.reader(io_string)

        for i, row in enumerate(reader):
            if not row or (len(row) == 1 and not row[0].strip()):
                continue  # Skip empty rows

            if row[0].strip().lower() == "query name":
                continue  # Skip header

            name = row[0] if len(row) > 0 else ""
            prompt = row[1] if len(row) > 1 else name  # If only one column, use it as both name and prompt
            output_format = row[2] if len(row) > 2 else ""

            AnalysisQuery.objects.create(
                analysis=analysis, name=name, prompt=prompt, output_format=output_format, order=i
            )


class SessionChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        label = f"{obj.external_id} - {obj.participant.identifier}" if obj.participant else str(obj.external_id)
        url = reverse(
            "experiments:experiment_session_view",
            kwargs={
                "team_slug": obj.team.slug,
                "experiment_id": obj.experiment.public_id,
                "session_id": obj.external_id,
            },
        )
        return format_html('<a class="link" href="{}" target="_blank">{}</a>', url, label)
