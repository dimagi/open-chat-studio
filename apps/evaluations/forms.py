from django import forms

from apps.evaluations.models import EvaluationConfig, EvaluationDataset, Evaluator
from apps.experiments.models import Experiment, ExperimentSession


class EvaluationConfigForm(forms.ModelForm):
    class Meta:
        model = EvaluationConfig
        fields = ("name", "experiment", "evaluators")

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team
        self.fields["experiment"].queryset = Experiment.objects.filter(team=team)


class EvaluatorForm(forms.ModelForm):
    class Meta:
        model = Evaluator
        fields = ("name", "type", "params")

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team


class EvaluationDatasetForm(forms.ModelForm):
    class Meta:
        model = EvaluationDataset
        fields = ("message_type", "version", "sessions")

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

        # TODO Filter sessions by version?
        self.fields["version"].queryset = Experiment.objects.filter(team=team)
        self.fields["sessions"].queryset = ExperimentSession.objects.filter(team=team)
