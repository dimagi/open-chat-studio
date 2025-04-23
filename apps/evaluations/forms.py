from django import forms

from apps.evaluations.models import EvaluationConfig, Evaluator
from apps.experiments.models import Experiment


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
