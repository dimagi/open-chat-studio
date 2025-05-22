import json

from django import forms

from apps.evaluations.models import (
    DatasetMessageTypeChoices,
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    Evaluator,
)


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
    class Meta:
        model = EvaluationDataset
        fields = ("name", "message_type")

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

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
                if msg.get("message_type") not in DatasetMessageTypeChoices:
                    raise forms.ValidationError(f"Message type for {msg.get('content')} is incorrect")

    def save(self, commit=True):
        dataset = super().save(commit=False)

        if commit:
            dataset.save()

            dataset_messages = self.data.get("messages_json")
            if dataset_messages:
                try:
                    message_dicts = json.loads(dataset_messages)
                except json.JSONDecodeError as err:
                    raise forms.ValidationError("Could not parse messages.") from err

                instances = [
                    EvaluationMessage(
                        id=m.get("id"),
                        content=m.get("content", "").strip(),
                        message_type=m.get("message_type"),
                    )
                    for m in message_dicts
                ]
                EvaluationMessage.objects.bulk_create(instances)
                dataset.messages.set(instances)

        return dataset


class EvaluationMessageForm(forms.ModelForm):
    class Meta:
        model = EvaluationMessage
        fields = ("message_type", "content")
