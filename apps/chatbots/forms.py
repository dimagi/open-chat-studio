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


class CopyChatbotForm(forms.Form):
    new_name = forms.CharField(
        max_length=255,
        required=True,
    )
