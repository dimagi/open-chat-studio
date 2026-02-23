from django import forms
from django.db import transaction
from waffle import flag_is_active

from apps.experiments.models import Experiment, SyntheticVoice
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
    participant_allowlist = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = Experiment
        fields = [
            "name",
            "description",
            "voice_provider",
            "synthetic_voice",
            "voice_response_behaviour",
            "echo_transcript",
            "trace_provider",
            "debug_mode_enabled",
            "conversational_consent_enabled",
            "consent_form",
            "pre_survey",
            "post_survey",
            "participant_allowlist",
            "seed_message",
            "file_uploads_enabled",
        ]
        labels = {
            "participant_allowlist": "Participant allowlist",
            "voice_provider": "Speech Provider",
            "voice_response_behaviour": "Response Provider",
        }
        help_texts = {
            "debug_mode_enabled": (
                "Enabling this tags each AI message in the web UI with the bot responsible for generating it. "
                "This is applicable only for router bots."
            ),
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        team = request.team
        exclude_services = [SyntheticVoice.OpenAIVoiceEngine]
        if flag_is_active(request, "flag_open_ai_voice_engine"):
            exclude_services = []
        self.fields["voice_provider"].queryset = team.voiceprovider_set.exclude(
            syntheticvoice__service__in=exclude_services
        )
        self.fields["synthetic_voice"].queryset = SyntheticVoice.get_for_team(team, exclude_services)
        self.fields["trace_provider"].queryset = team.traceprovider_set
        self.fields["pre_survey"].queryset = team.survey_set.exclude(is_version=True)
        self.fields["post_survey"].queryset = team.survey_set.exclude(is_version=True)
        self.fields["synthetic_voice"].widget.template_name = "django/forms/widgets/select_dynamic.html"  # ty: ignore[invalid-assignment]
        self.fields["voice_provider"].widget.attrs = {
            "x-model.fill": "voiceProvider",
        }

    def clean_participant_allowlist(self):
        cleaned_identifiers = []
        identifiers = filter(None, self.cleaned_data["participant_allowlist"].split(","))
        for identifier in identifiers:
            cleaned_identifiers.append(identifier.replace(" ", ""))
        return cleaned_identifiers

    @transaction.atomic()
    def save(self, commit=True):
        experiment = super().save(commit=False)

        if commit:
            experiment.save()
            self.save_m2m()
        return experiment


class CopyChatbotForm(forms.Form):
    new_name = forms.CharField(
        max_length=255,
        required=True,
    )
