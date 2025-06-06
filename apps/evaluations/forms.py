import importlib
import json

from django import forms
from pydantic import ValidationError as PydanticValidationError

from apps.chat.models import ChatMessageType
from apps.evaluations.models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    Evaluator,
)
from apps.experiments.models import ExperimentSession


class EvaluationConfigForm(forms.ModelForm):
    class Meta:
        model = EvaluationConfig
        fields = ("name", "dataset", "message_type", "evaluators")
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

    def clean_params(self):
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

            # Validate parameters against the Pydantic model
            evaluator_class(**params)

        except AttributeError as err:
            raise forms.ValidationError(f"Unknown evaluator type: {evaluator_type}") from err
        except PydanticValidationError as err:
            # Convert Pydantic errors to Django form errors
            error_messages = []
            for error in err.errors():
                field_name = error["loc"][0] if error["loc"] else "unknown"
                message = error["msg"]
                error_messages.append(f"{field_name}: {message}")
            raise forms.ValidationError(f"Parameter validation failed: {'; '.join(error_messages)}") from err

        return params


class EvaluationMessageForm(forms.ModelForm):
    class Meta:
        model = EvaluationMessage
        fields = ("human_message_content", "ai_message_content", "context")


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

            cleaned_data["message_pairs"] = message_pairs

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
                # Get all sessions to clone from
                sessions = ExperimentSession.objects.filter(team=self.team, external_id__in=session_ids).select_related(
                    "chat"
                )

                for session in sessions:
                    messages = list(session.chat.messages.order_by("created_at"))
                    # Store each message as we see it to add as the history for the next messages
                    history = []
                    i = 0
                    while i < len(messages) - 1:
                        current_msg = messages[i]
                        next_msg = messages[i + 1]
                        if (
                            current_msg.message_type == ChatMessageType.HUMAN
                            and next_msg.message_type == ChatMessageType.AI
                        ):
                            evaluation_message = EvaluationMessage(
                                human_chat_message=current_msg,
                                human_message_content=current_msg.content,
                                ai_chat_message=next_msg,
                                ai_message_content=next_msg.content,
                                context={
                                    "current_datetime": current_msg.created_at.isoformat(),
                                    "history": "\n".join(history),
                                },
                                metadata={
                                    "session_id": session.external_id,
                                    "experiment_name": session.experiment.name,
                                    "participant_identifier": session.participant.identifier
                                    if session.participant
                                    else None,
                                },
                            )
                            evaluation_messages.append(evaluation_message)

                            history.append(f"{current_msg.get_message_type_display()}: {current_msg.content}")
                            history.append(f"{next_msg.get_message_type_display()}: {next_msg.content}")

                            i += 2
                        else:
                            # If there is not a (human, ai) pair, move on.
                            i += 1

        elif mode == "manual":
            message_pairs = self.cleaned_data.get("message_pairs", [])
            for pair in message_pairs:
                context = {}
                if pair.get("context"):
                    try:
                        context = json.loads(pair["context"]) if isinstance(pair["context"], str) else pair["context"]
                    except json.JSONDecodeError:
                        context = {"raw_context": pair["context"]}

                evaluation_message = EvaluationMessage(
                    human_message_content=pair["human"].strip(),
                    ai_message_content=pair["ai"].strip(),
                    context=context,
                    metadata={"created_mode": "manual"},
                )
                evaluation_messages.append(evaluation_message)

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
