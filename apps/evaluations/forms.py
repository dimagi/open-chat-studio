import json

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

    def clean(self):
        super().clean()

        raw_json = self.data.get("messages_json")
        if raw_json:
            try:
                messages = json.loads(raw_json)
            except json.JSONDecodeError as err:
                raise forms.ValidationError("Messages data is invalid JSON.") from err

            for msg in messages:
                if not msg.get("content") or not msg.get("message_type"):
                    raise forms.ValidationError("Each message must have type and content.")

    def save(self, commit=True):
        dataset = super().save(commit=False)

        dataset = super().save(commit=False)

        if commit:
            dataset.save()

            raw_json = self.data.get("messages_json")
            if raw_json:
                try:
                    message_dicts = json.loads(raw_json)
                except json.JSONDecodeError as err:
                    raise forms.ValidationError("Could not parse messages.") from err

                # Optional: validate content + message_type per message_dict here
                instances = [
                    EvaluationMessage(
                        content=m["content"].strip(),
                        message_type=m["message_type"],
                    )
                    for m in message_dicts
                    if m.get("content") and m.get("message_type")
                ]
                EvaluationMessage.objects.bulk_create(instances)
                dataset.messages.set(instances)

        return dataset


class EvaluationMessageForm(forms.ModelForm):
    class Meta:
        model = EvaluationMessage
        fields = ("message_type", "content")
