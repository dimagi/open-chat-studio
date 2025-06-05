from django import forms

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


class EvaluationMessageForm(forms.ModelForm):
    class Meta:
        model = EvaluationMessage
        fields = ("human_message_content", "ai_message_content", "context")


class EvaluationDatasetForm(forms.ModelForm):
    """Form for creating evaluation datasets from multiple experiment sessions."""

    session_ids = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
        help_text="Comma-separated list of session external IDs to clone messages from",
    )

    class Meta:
        model = EvaluationDataset
        fields = ("name",)

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

    def clean_session_ids(self):
        session_ids = set(self.data.get("session_ids", "").split(","))
        session_ids.discard("")  # Remove empty strings

        if not session_ids:
            raise forms.ValidationError("At least one session must be selected.")

        existing_sessions = ExperimentSession.objects.filter(team=self.team, external_id__in=session_ids).values_list(
            "external_id", flat=True
        )

        missing_sessions = set(session_ids) - set(existing_sessions)
        if missing_sessions:
            raise forms.ValidationError(
                "The following sessions do not exist or you don't have permission to access them: "
                f"{', '.join(missing_sessions)}"
            )

        return session_ids

    def save(self, commit=True):
        """Create dataset and clone messages from selected sessions."""
        dataset = super().save(commit=False)

        if not commit:
            return dataset

        dataset.save()

        session_ids = self.cleaned_data.get("session_ids", [])
        if not session_ids:
            return dataset

        # Get all sessions to clone from
        sessions = ExperimentSession.objects.filter(team=self.team, external_id__in=session_ids).select_related("chat")

        evaluation_messages = []

        for session in sessions:
            messages = list(session.chat.messages.order_by("created_at"))
            # Store each message as we see it to add as the history for the next messages
            history = []
            i = 0
            while i < len(messages) - 1:
                current_msg = messages[i]
                next_msg = messages[i + 1]
                if current_msg.message_type == ChatMessageType.HUMAN and next_msg.message_type == ChatMessageType.AI:
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
                            "participant_identifier": session.participant.identifier if session.participant else None,
                        },
                    )
                    evaluation_messages.append(evaluation_message)

                    history.append(f"{current_msg.get_message_type_display()}: {current_msg.content}")
                    history.append(f"{next_msg.get_message_type_display()}: {next_msg.content}")

                    i += 2
                else:
                    # If there is not a (human, ai) pair, move on.
                    i += 1

        if evaluation_messages:
            EvaluationMessage.objects.bulk_create(evaluation_messages)
            dataset.messages.set(evaluation_messages)

        return dataset
