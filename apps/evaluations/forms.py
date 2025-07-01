import importlib
import json

from django import forms
from pydantic import ValidationError as PydanticValidationError

from apps.evaluations.models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationMessageContent,
    Evaluator,
)
from apps.experiments.models import ExperimentSession


class EvaluationConfigForm(forms.ModelForm):
    class Meta:
        model = EvaluationConfig
        fields = ("name", "dataset", "evaluators")
        widgets = {
            "evaluators": forms.MultipleHiddenInput(),
        }

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team
        self.fields["dataset"].queryset = EvaluationDataset.objects.filter(team=team)
        self.fields["evaluators"].queryset = Evaluator.objects.filter(team=team)


class EvaluatorForm(forms.ModelForm):
    class Meta:
        model = Evaluator
        fields = ("name", "type", "params")
        widgets = {
            "type": forms.HiddenInput(),
            "params": forms.HiddenInput(),
        }

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

    def clean(self):
        cleaned_data = super().clean()

        params = self.cleaned_data.get("params")
        evaluator_type = self.cleaned_data.get("type")

        if not evaluator_type:
            raise forms.ValidationError("Missing evaluator type")

        if isinstance(params, str):
            try:
                params = json.loads(params) or {}
            except json.JSONDecodeError as err:
                raise forms.ValidationError("Invalid JSON format for parameters") from err

        try:
            evaluators_module = importlib.import_module("apps.evaluations.evaluators")
            evaluator_class = getattr(evaluators_module, evaluator_type)

            evaluator_class(**params)

        except AttributeError as err:
            raise forms.ValidationError(f"Unknown evaluator type: {evaluator_type}") from err
        except PydanticValidationError as err:
            error_messages = []
            for error in err.errors():
                field_name = error["loc"][0] if error["loc"] else "unknown"
                message = error["msg"]
                error_messages.append(f"{field_name.replace('_', ' ').title()}: {message}")
            raise forms.ValidationError(f"{', '.join(error_messages)}") from err

        return cleaned_data


class EvaluationMessageForm(forms.ModelForm):
    class Meta:
        model = EvaluationMessage
        fields = ("input", "output", "context")


class EvaluationDatasetForm(forms.ModelForm):
    """Form for creating evaluation datasets."""

    MODE_CHOICES = [
        ("clone", "Clone from sessions"),
        ("manual", "Create manually"),
    ]

    mode = forms.ChoiceField(
        choices=MODE_CHOICES, widget=forms.RadioSelect, initial="clone", label="Dataset creation mode"
    )

    session_ids = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )

    messages_json = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )

    class Meta:
        model = EvaluationDataset
        fields = ("name",)

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

    def clean_name(self):
        name = self.cleaned_data.get("name")
        if name and EvaluationDataset.objects.filter(name=name, team=self.team).exists():
            raise forms.ValidationError("A dataset with this name already exists in your team.")

        return name

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get("mode")

        if mode == "clone":
            session_ids_str = self.data.get("session_ids", "")
            session_ids = set(session_ids_str.split(","))
            session_ids.discard("")  # Remove empty strings

            if not session_ids:
                raise forms.ValidationError("At least one session must be selected when cloning from sessions.")

            existing_sessions = ExperimentSession.objects.filter(
                team=self.team, external_id__in=session_ids
            ).values_list("external_id", flat=True)

            missing_sessions = set(session_ids) - set(existing_sessions)
            if missing_sessions:
                raise forms.ValidationError(
                    "The following sessions do not exist or you don't have permission to access them: "
                    f"{', '.join(missing_sessions)}"
                )

            cleaned_data["session_ids"] = session_ids

        elif mode == "manual":
            messages_json = self.data.get("messages_json", "")
            cleaned_data["message_pairs"] = []
            if not messages_json:
                raise forms.ValidationError("At least one message pair must be added when creating manually.")

            try:
                message_pairs = json.loads(messages_json)
            except json.JSONDecodeError as err:
                raise forms.ValidationError("Messages data is invalid JSON.") from err

            if not isinstance(message_pairs, list) or len(message_pairs) == 0:
                raise forms.ValidationError("At least one message pair must be added.")

            for i, pair in enumerate(message_pairs):
                if not isinstance(pair, dict):
                    raise forms.ValidationError(f"Message pair {i + 1} is not a valid object.")
                if not pair.get("human", "").strip():
                    raise forms.ValidationError(f"Message pair {i + 1} is missing human message content.")
                if not pair.get("ai", "").strip():
                    raise forms.ValidationError(f"Message pair {i + 1} is missing AI message content.")
                if pair.get("context"):
                    try:
                        json.loads(pair.get("context", "{}"))
                    except json.JSONDecodeError as err:
                        raise forms.ValidationError(f"Context for pair {i + 1} has malformed JSON") from err

                cleaned_data["message_pairs"].append(
                    {
                        "human": EvaluationMessageContent(
                            content=pair.get("human", "").strip(), role="human"
                        ).model_dump(),
                        "ai": EvaluationMessageContent(content=pair.get("ai", "").strip(), role="ai").model_dump(),
                        "context": pair.get("context"),
                    }
                )

        return cleaned_data

    def save(self, commit=True):
        """Create dataset based on the selected mode."""
        dataset = super().save(commit=False)

        if not commit:
            return dataset

        dataset.save()

        mode = self.cleaned_data.get("mode")
        evaluation_messages = []

        if mode == "clone":
            session_ids = self.cleaned_data.get("session_ids", [])
            if session_ids:
                evaluation_messages = EvaluationMessage.create_from_sessions(self.team, session_ids)
        elif mode == "manual":
            evaluation_messages = [
                EvaluationMessage(
                    input=pair["human"],
                    output=pair["ai"],
                    context=pair["context"],
                    metadata={"created_mode": "manual"},
                )
                for pair in self.cleaned_data.get("message_pairs", [])
            ]

        if evaluation_messages:
            EvaluationMessage.objects.bulk_create(evaluation_messages)
            dataset.messages.set(evaluation_messages)

        return dataset


class EvaluationDatasetEditForm(forms.ModelForm):
    """Simple form for editing existing evaluation datasets (name only)."""

    class Meta:
        model = EvaluationDataset
        fields = ("name",)

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

    def clean_name(self):
        name = self.cleaned_data.get("name")
        if name:
            duplicate_query = EvaluationDataset.objects.filter(name=name, team=self.team)
            if self.instance.pk:
                duplicate_query = duplicate_query.exclude(pk=self.instance.pk)

            if duplicate_query.exists():
                raise forms.ValidationError("A dataset with this name already exists in your team.")

        return name
