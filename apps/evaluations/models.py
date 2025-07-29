from __future__ import annotations

import importlib
from collections import defaultdict
from functools import cached_property
from typing import TYPE_CHECKING, Literal, Self

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel as PydanticBaseModel

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession
from apps.teams.models import BaseTeamModel, Team
from apps.utils.models import BaseModel

if TYPE_CHECKING:
    from apps.evaluations.evaluators import EvaluatorResult


class ExperimentVersionSelection(models.TextChoices):
    """Choices for experiment version selection including sentinel values"""

    SPECIFIC = "specific", "Specific Version"
    LATEST_WORKING = "latest_working", "Latest Working Version"
    LATEST_PUBLISHED = "latest_published", "Latest Published Version"


class Evaluator(BaseTeamModel):
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=128)  # The evaluator type, should be one from evaluators.py
    params = models.JSONField(
        default=dict
    )  # This is different for each evaluator. Usage is similar to how we define Nodes in pipelines

    def __str__(self):
        try:
            label = self.evaluator.model_config["evaluator_schema"].label
        except KeyError:
            label = self.type
        return f"{self.name} ({label})"

    @cached_property
    def evaluator(self):
        module = importlib.import_module("apps.evaluations.evaluators")
        return getattr(module, self.type)

    def run(self, message: EvaluationMessage, generated_response: str) -> EvaluatorResult:
        return self.evaluator(**self.params).run(message, generated_response)

    def get_absolute_url(self):
        return reverse("evaluations:evaluator_edit", args=[self.team.slug, self.id])


class EvaluationMessageContent(PydanticBaseModel):
    content: str
    role: Literal["human", "ai"]


class EvaluationMessage(BaseModel):
    input_chat_message = models.ForeignKey(
        ChatMessage, on_delete=models.SET_NULL, null=True, blank=True, related_name="human_evaluation_messages"
    )
    # null when it is generated manually
    expected_output_chat_message = models.ForeignKey(
        ChatMessage, on_delete=models.SET_NULL, null=True, blank=True, related_name="ai_evaluation_messages"
    )
    # null when it is generated manually

    input = models.JSONField(default=dict)
    output = models.JSONField(default=dict)
    context = models.JSONField(default=dict)
    history = models.JSONField(default=list)  # List of message objects with message_type, content, summary

    metadata = models.JSONField(default=dict)

    def __str__(self):
        input_role = self.input.get("role", "(human)").title()
        input_content = self.input.get("content", "no content")
        output_role = self.output.get("role", "(ai)").title()
        output_content = self.output.get("content", "no content")
        return f"{input_role}: {input_content}, {output_role}: {output_content}"

    @classmethod
    def create_from_sessions(cls, team: Team, external_session_ids) -> list[Self]:
        new_messages = []
        all_messages = (
            ChatMessage.objects.filter(
                chat__experiment_session__external_id__in=external_session_ids,
                chat__experiment_session__team=team,
            )
            .select_related(
                "chat__experiment_session",
                "chat__experiment_session__participant",
                "chat__experiment_session__experiment",
            )
            .order_by("chat__experiment_session__created_at", "created_at")
            .all()
        )

        messages_by_session = defaultdict(list)
        for message in all_messages:
            messages_by_session[message.chat.experiment_session.external_id].append(message)

        for session_id, messages in messages_by_session.items():
            # Iterate per session so history gets cleared
            history = []  # List of message dicts
            i = 0
            while i < len(messages) - 1:
                current_msg = messages[i]
                next_msg = messages[i + 1]
                if current_msg.message_type == ChatMessageType.HUMAN and next_msg.message_type == ChatMessageType.AI:
                    session = current_msg.chat.experiment_session
                    evaluation_message = EvaluationMessage(
                        input_chat_message=current_msg,
                        input=EvaluationMessageContent(content=current_msg.content, role="human").model_dump(),
                        expected_output_chat_message=next_msg,
                        output=EvaluationMessageContent(content=next_msg.content, role="ai").model_dump(),
                        context={
                            "current_datetime": current_msg.created_at.isoformat(),
                        },
                        history=[msg.copy() for msg in history],  # Store as JSON list
                        metadata={
                            "session_id": session_id,
                            "experiment_id": str(session.experiment.public_id),
                        },
                    )
                    new_messages.append(evaluation_message)

                    history.append(
                        {
                            "message_type": current_msg.message_type,
                            "content": current_msg.content,
                            "summary": getattr(current_msg, "summary", None),
                        }
                    )
                    history.append(
                        {
                            "message_type": next_msg.message_type,
                            "content": next_msg.content,
                            "summary": getattr(next_msg, "summary", None),
                        }
                    )

                    i += 2
                else:
                    # If there is not a (human, ai) pair, move on.
                    i += 1
        return new_messages

    def as_langchain_messages(self) -> list[BaseMessage]:
        """
        Converts this message instance into a list of Langchain `BaseMessage` objects.
        """
        return [
            self.as_human_langchain_message(),
            self.as_ai_langchain_message(),
        ]

    def as_human_langchain_message(self) -> BaseMessage:
        return HumanMessage(
            content=self.input["content"],
            additional_kwargs={"id": self.id, "chat_message_id": self.input_chat_message_id},
        )

    def as_ai_langchain_message(self) -> BaseMessage:
        return AIMessage(
            content=self.output["content"],
            additional_kwargs={"id": self.id, "chat_message_id": self.expected_output_chat_message_id},
        )

    @property
    def full_history(self) -> str:
        """
        Generate a full history string from the JSON history data.
        This is used for backward compatibility with the LlmEvaluator.
        """
        if not self.history:
            return ""

        history_lines = []
        for message in self.history:
            message_type = message.get("message_type", "")
            content = message.get("content", "")
            # Use ChatMessage's get_message_type_display method
            display_type = ChatMessage(message_type=message_type).get_message_type_display()
            history_lines.append(f"{display_type}: {content}")

        return "\n".join(history_lines)


class EvaluationDataset(BaseTeamModel):
    name = models.CharField(max_length=255)
    messages = models.ManyToManyField(EvaluationMessage)

    def __str__(self):
        return f"{self.name} ({self.messages.count()} messages)"

    def get_absolute_url(self):
        return reverse("evaluations:dataset_edit", args=[self.team.slug, self.id])

    class Meta:
        unique_together = ("name", "team")


class EvaluationConfig(BaseTeamModel):
    name = models.CharField(max_length=255)
    evaluators = models.ManyToManyField(Evaluator)
    dataset = models.ForeignKey(EvaluationDataset, on_delete=models.CASCADE)
    experiment_version = models.ForeignKey(
        "experiments.Experiment",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text=("Specific chatbot version to use for evaluation. If not set, will skip generation."),
    )
    # Store the base experiment for sentinel value resolution
    base_experiment = models.ForeignKey(
        "experiments.Experiment",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="evaluations_as_base",
        help_text=("Base chatbot used when experiment_version is a sentinel value."),
    )
    # Store sentinel value if using latest working/published
    version_selection_type = models.CharField(
        max_length=50,
        choices=ExperimentVersionSelection.choices,
        default=ExperimentVersionSelection.SPECIFIC,
        help_text=("Type of version selection: specific, latest_working, or latest_published"),
    )

    def __str__(self):
        return f"EvaluationConfig ({self.name})"

    def get_generation_experiment_version(self):
        """Resolve the actual experiment version based on selection type"""
        if self.version_selection_type == ExperimentVersionSelection.SPECIFIC:
            return self.experiment_version

        if not self.base_experiment:
            return None

        if self.version_selection_type == ExperimentVersionSelection.LATEST_WORKING:
            # Get the working version (the one with working_version_id=None)
            working_version_id = self.base_experiment.working_version_id or self.base_experiment.id
            return Experiment.objects.filter(team=self.team, id=working_version_id).first()

        elif self.version_selection_type == ExperimentVersionSelection.LATEST_PUBLISHED:
            # Get the latest published version (highest version_number with is_default_version=True)
            working_version_id = self.base_experiment.working_version_id or self.base_experiment.id
            return (
                Experiment.objects.filter(
                    team=self.team, working_version_id=working_version_id, is_default_version=True
                )
                .order_by("-version_number")
                .first()
            )

        return None

    def get_absolute_url(self):
        return reverse("evaluations:evaluation_runs_home", args=[self.team.slug, self.id])

    def run(self) -> EvaluationRun:
        """Runs the evaluation asynchronously using Celery"""
        generation_experiment = self.get_generation_experiment_version()
        run = EvaluationRun.objects.create(
            team=self.team, config=self, generation_experiment=generation_experiment, status=EvaluationRunStatus.PENDING
        )

        from apps.evaluations.tasks import run_evaluation_task

        result = run_evaluation_task.delay(run.id)
        run.job_id = result.id
        run.save(update_fields=["job_id"])

        return run


class EvaluationRunStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class EvaluationRun(BaseTeamModel):
    config = models.ForeignKey(EvaluationConfig, on_delete=models.CASCADE)
    generation_experiment = models.ForeignKey(
        "experiments.Experiment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The experiment version used for generation during this evaluation run",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )  # if manually triggered, who did it
    status = models.CharField(max_length=20, choices=EvaluationRunStatus.choices, default=EvaluationRunStatus.PENDING)
    job_id = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)

    def __str__(self):
        return f"EvaluationRun ({self.created_at} - {self.finished_at})"

    def get_absolute_url(self):
        return reverse("evaluations:evaluation_results_home", args=[self.team.slug, self.config_id, self.pk])

    def mark_complete(self, save=True):
        self.finished_at = timezone.now()
        self.status = EvaluationRunStatus.COMPLETED
        if save:
            self.save(update_fields=["finished_at", "status"])

    def get_table_data(self):
        results = self.results.select_related("message", "evaluator", "session").all()
        table_by_message = defaultdict(dict)
        for result in results:
            table_by_message[result.message.id].update(
                {
                    "Dataset Input": result.message.input.get("content", ""),
                    "Dataset Output": result.message.output.get("content", ""),
                    "Generated Response": result.output.get("generated_response", ""),
                    **{
                        f"{key} ({result.evaluator.name})": value
                        for key, value in result.output.get("result", {}).items()
                    },
                    **{f"{key}": value for key, value in result.message.context.items()},
                    "session": result.session.external_id if result.session_id else "",
                }
            )
            if result.output.get("error"):
                table_by_message[result.message.id]["error"] = result.output.get("error")
        return table_by_message.values()


class EvaluationResult(BaseTeamModel):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.CASCADE)
    message = models.ForeignKey(EvaluationMessage, on_delete=models.CASCADE)
    run = models.ForeignKey(EvaluationRun, on_delete=models.CASCADE, related_name="results")
    session = models.ForeignKey(ExperimentSession, on_delete=models.SET_NULL, null=True)
    output = models.JSONField()

    def __str__(self):
        return f"EvaluatorResult for Evaluator {self.evaluator_id}"
