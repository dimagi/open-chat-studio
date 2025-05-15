from django import forms
from django.db import transaction

from apps.experiments.models import Experiment
from apps.pipelines.models import Pipeline
from apps.service_providers.utils import get_first_llm_provider_by_team, get_first_llm_provider_model


class ChatbotForm(forms.ModelForm):
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)

    class Meta:
        model = Experiment
        fields = [
            "name",
            "description",
        ]

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request

    @transaction.atomic()
    def save(self, commit=True):
        team_id = self.request.team.id
        llm_provider = get_first_llm_provider_by_team(team_id)
        llm_provider_model = None
        if llm_provider:
            llm_provider_model = get_first_llm_provider_model(llm_provider, team_id)
        pipeline = Pipeline.create_default_pipeline_with_name(
            self.request.team, self.cleaned_data["name"], llm_provider.id if llm_provider else None, llm_provider_model
        )
        experiment = super().save(commit=False)
        experiment.team = self.request.team
        experiment.owner = self.request.user
        experiment.pipeline = pipeline
        if commit:
            experiment.save()
            self.save_m2m()
        return experiment


class ChatbotSettingsForm(forms.ModelForm):
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    seed_message = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)

    class Meta:
        model = Experiment
        fields = [
            "description",
            "voice_provider",
            "synthetic_voice",
            "voice_response_behaviour",
            "echo_transcript",
            "use_processor_bot_voice",
            "trace_provider",
            "debug_mode_enabled",
            "conversational_consent_enabled",
            "pre_survey",
            "post_survey",
            "participant_allowlist",
            "seed_message",
        ]
        labels = {"participant_allowlist": "Participant allowlist"}
        help_texts = {
            "use_processor_bot_voice": (
                "In a multi-bot setup, use the configured voice of the bot that generated the output. If it doesn't "
                "have one, the router bot's voice will be used."
            ),
            "participant_allowlist": (
                "Separate identifiers with a comma. Phone numbers should be in E164 format e.g. +27123456789"
            ),
            "debug_mode_enabled": (
                "Enabling this tags each AI message in the web UI with the bot responsible for generating it. "
                "This is applicable only for router bots."
            ),
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request

    def clean_participant_allowlist(self):
        raw_text = self.cleaned_data["participant_allowlist"]
        identifiers = [line.strip() for line in raw_text.split("\n") if line.strip()]
        cleaned_identifiers = []
        for identifier in identifiers:
            cleaned_identifiers.append(identifier.replace(" ", ""))
        print(cleaned_identifiers)
        return cleaned_identifiers

    @transaction.atomic()
    def save(self, commit=True):
        experiment = super().save(commit=False)

        if commit:
            experiment.save()
            self.save_m2m()
        return experiment
