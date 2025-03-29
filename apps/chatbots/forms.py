from django import forms
from django.db import transaction

from apps.experiments.models import Experiment
from apps.pipelines.models import Pipeline


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
        pipeline = Pipeline.create_pipeline_with_name(self.request.team, self.cleaned_data["name"])
        experiment = super().save(commit=False)
        experiment.team = self.request.team
        experiment.owner = self.request.user
        experiment.pipeline = pipeline
        if commit:
            experiment.save()
            self.save_m2m()
        return experiment
