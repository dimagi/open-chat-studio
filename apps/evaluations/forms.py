from django import forms

from apps.evaluations.models import EvaluationConfig, EvaluationDataset, EvaluationMessage, Evaluator
from apps.experiments.models import ExperimentSession


class EvaluationConfigForm(forms.ModelForm):
    class Meta:
        model = EvaluationConfig
        fields = ("name", "dataset", "evaluators")

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team
        self.fields["dataset"].queryset = EvaluationDataset.objects.filter(team=team)
        self.fields["evaluators"].queryset = Evaluator.objects.filter(team=team)


class EvaluatorForm(forms.ModelForm):
    class Meta:
        model = Evaluator
        fields = ("name", "type", "params")

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team


class EvaluationDatasetForm(forms.ModelForm):
    session = forms.ModelChoiceField(queryset=None, required=True, help_text="Choose a session to copy messages from")

    class Meta:
        model = EvaluationDataset
        fields = ("name", "message_type", "session")

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

        self.fields["session"].queryset = ExperimentSession.objects.filter(team=team).order_by(
            "experiment__name", "-created_at"
        )
        self.fields["session"].label_from_instance = lambda session: (
            f"{session.experiment.name} – {session.created_at.strftime('%Y-%m-%d %H:%M')} ({session.participant})"
        )

    def save(self, commit=True):
        dataset = super().save(commit=False)

        if commit:
            dataset.save()

        session = self.cleaned_data["session"]

        messages = []
        for message in session.chat.messages.all():
            evaluation_message = EvaluationMessage.objects.create(
                chat_message=message,
                message_type=message.message_type,
                content=message.content,
            )
            messages.append(evaluation_message)

        dataset.messages.set(messages)

        return dataset
