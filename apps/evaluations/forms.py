import json

from django import forms

from apps.chat.models import ChatMessage
from apps.evaluations.models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    Evaluator,
)


class EvaluationConfigForm(forms.ModelForm):
    class Meta:
        model = EvaluationConfig
        fields = ("name", "dataset", "message_type", "evaluators")

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
        fields = ("name",)

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

    def clean(self):
        super().clean()
        raw_json = self.data.get("messages_json")

        if raw_json:
            try:
                message_pairs = json.loads(raw_json)
            except json.JSONDecodeError as err:
                raise forms.ValidationError("Messages data is invalid JSON.") from err

            if not isinstance(message_pairs, list):
                raise forms.ValidationError("Message data must be a list of human/AI pairs.")

            for pair in message_pairs:
                if not pair.get("human") or not pair.get("ai"):
                    raise forms.ValidationError("Each pair must include 'human' and 'ai' messages.")

    def save(self, commit=True):
        dataset = super().save(commit=False)

        if not commit:
            return dataset

        dataset.save()
        dataset_messages_json = self.data.get("messages_json")
        if not dataset_messages_json:
            return dataset

        try:
            message_pairs = json.loads(dataset_messages_json)
        except json.JSONDecodeError as err:
            raise forms.ValidationError("Could not parse message pairs.") from err

        evaluation_messages = []
        for pair in message_pairs:
            human_msg = pair["human"]
            human_chat_message = None
            ai_msg = pair["ai"]
            ai_chat_message = None
            context = pair.get("context", {})
            if human_msg["id"]:
                try:
                    human_chat_message = ChatMessage.objects.get(chat__team=self.team, id=human_msg["id"])
                except ChatMessage.DoesNotExist as err:
                    raise forms.ValidationError("The linked chat message does not exist") from err
            if ai_msg["id"]:
                try:
                    human_chat_message = ChatMessage.objects.get(chat__team=self.team, id=ai_msg["id"])
                except ChatMessage.DoesNotExist as err:
                    raise forms.ValidationError("The linked chat message does not exist") from err

            evaluation_message = EvaluationMessage(
                human_message_content=human_msg["content"].strip(),
                human_chat_message=human_chat_message,
                ai_message_content=ai_msg["content"].strip(),
                ai_chat_message=ai_chat_message,
                context=context,
            )
            evaluation_messages.append(evaluation_message)

        EvaluationMessage.objects.bulk_create(evaluation_messages)
        dataset.messages.set(evaluation_messages)
        return dataset


class EvaluationMessageForm(forms.ModelForm):
    class Meta:
        model = EvaluationMessage
        fields = ("human_message_content", "ai_message_content", "context")
