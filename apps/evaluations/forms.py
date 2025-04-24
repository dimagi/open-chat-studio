from django import forms

from apps.evaluations.models import EvaluationConfig, EvaluationDataset, Evaluator
from apps.experiments.models import ExperimentSession


class EvaluationConfigForm(forms.ModelForm):
    class Meta:
        model = EvaluationConfig
        fields = ("name", "dataset", "evaluators")

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team
        self.fields["dataset"].queryset = EvaluationDataset.objects.filter(team=team)


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
        fields = ("name", "message_type", "sessions")

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

        # TODO Filter sessions by experiment
        self.fields["sessions"].queryset = ExperimentSession.objects.filter(team=team)
