from __future__ import annotations

import importlib
from collections import defaultdict
from functools import cached_property
from typing import TYPE_CHECKING, Literal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel as PydanticBaseModel

from apps.chat.models import ChatMessage, ChatMessageType
from apps.evaluations.utils import make_evaluation_messages_from_sessions
from apps.experiments.models import ExperimentSession
from apps.teams.models import BaseTeamModel, Team
from apps.teams.utils import get_slug_for_team
from apps.utils.fields import SanitizedJSONField
from apps.utils.models import BaseModel

if TYPE_CHECKING:
    from apps.evaluations.evaluators import EvaluatorResult


class EvaluationRunStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class EvaluationRunType(models.TextChoices):
    FULL = "full", "Full"
    PREVIEW = "preview", "Preview"


class ExperimentVersionSelection(models.TextChoices):
    """Choices for experiment version selection including sentinel values"""

    SPECIFIC = "specific", "Specific Version"
    LATEST_WORKING = "latest_working", "Latest Working Version"
    LATEST_PUBLISHED = "latest_published", "Latest Published Version"


class Evaluator(BaseTeamModel):
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=128)  # The evaluator type, should be one from evaluators.py
    params = SanitizedJSONField(
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
        return reverse("evaluations:evaluator_edit", args=[get_slug_for_team(self.team_id), self.id])


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

    input = SanitizedJSONField(default=dict)
    output = SanitizedJSONField(default=dict)
    context = SanitizedJSONField(default=dict)
    history = SanitizedJSONField(default=list)  # List of message objects with message_type, content, summary

    participant_data = SanitizedJSONField(
        default=dict, blank=True, help_text="Participant data at the time of the message"
    )
    session_state = SanitizedJSONField(default=dict, blank=True, help_text="Session state at the time of the trace")

    metadata = SanitizedJSONField(default=dict)

    def __str__(self):
        input_role = self.input.get("role", "(human)").title()
        input_content = self.input.get("content", "no content")
        output_role = self.output.get("role", "(ai)").title()
        output_content = self.output.get("content", "no content")
        return f"{input_role}: {input_content}, {output_role}: {output_content}"

    @classmethod
    def create_from_sessions(
        cls, team: Team, external_session_ids, filtered_session_ids=None, filter_params=None, timezone=None
    ) -> list[EvaluationMessage]:
        from apps.experiments.filters import ChatMessageFilter

        base_queryset = (
            ChatMessage.objects.filter(
                chat__experiment_session__team=team,
            )
            .select_related(
                "chat__experiment_session",
                "chat__experiment_session__participant",
                "chat__experiment_session__experiment",
            )
            .prefetch_related("comments", "tags")
            .order_by("chat__experiment_session__created_at", "created_at")
        )

        message_ids_per_session = defaultdict(list)
        if external_session_ids:
            regular_messages = base_queryset.filter(chat__experiment_session__external_id__in=external_session_ids)
            for session_id, message_id in regular_messages.values_list("chat__experiment_session__external_id", "id"):
                message_ids_per_session[session_id].append(message_id)

        if filtered_session_ids and filter_params:
            filtered_messages = base_queryset.filter(chat__experiment_session__external_id__in=filtered_session_ids)
            message_filter = ChatMessageFilter()
            filtered_messages = message_filter.apply(filtered_messages, filter_params, timezone)
            for session_id, message_id in filtered_messages.values_list("chat__experiment_session__external_id", "id"):
                message_ids_per_session[session_id].append(message_id)

        if not message_ids_per_session:
            return []

        return make_evaluation_messages_from_sessions(message_ids_per_session)

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
            display_type = ChatMessageType(message_type).role
            history_lines.append(f"{display_type}: {content}")

        return "\n".join(history_lines)

    def as_result_dict(self) -> dict:
        """Returns a dict representation to be stored in any evaluator result"""
        return {
            "input": self.input,
            "output": self.output,
            "context": self.context,
            "history": self.history,
            "metadata": self.metadata,
        }


class EvaluationDataset(BaseTeamModel):
    name = models.CharField(max_length=255)
    messages = models.ManyToManyField(EvaluationMessage)

    def __str__(self):
        return f"{self.name} ({self.messages.count()} messages)"

    def get_absolute_url(self):
        return reverse("evaluations:dataset_edit", args=[get_slug_for_team(self.team_id), self.id])

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
            return self.base_experiment.get_working_version()
        elif self.version_selection_type == ExperimentVersionSelection.LATEST_PUBLISHED:
            return self.base_experiment.default_version
        return None

    def get_absolute_url(self):
        return reverse("evaluations:evaluation_runs_home", args=[get_slug_for_team(self.team_id), self.id])

    def run(self, run_type=EvaluationRunType.FULL) -> EvaluationRun:
        """Runs the evaluation asynchronously using Celery"""
        generation_experiment = self.get_generation_experiment_version()
        run = EvaluationRun.objects.create(
            team=self.team,
            config=self,
            generation_experiment=generation_experiment,
            status=EvaluationRunStatus.PENDING,
            type=run_type,
        )

        from apps.evaluations.tasks import run_evaluation_task

        run_evaluation_task.delay(run.id)
        return run

    def run_preview(self) -> EvaluationRun:
        """Runs a preview evaluation on a sample of the dataset"""
        return self.run(run_type=EvaluationRunType.PREVIEW)


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
    type = models.CharField(
        max_length=20, choices=EvaluationRunType.choices, default=EvaluationRunType.FULL, db_index=True
    )
    job_id = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)

    def __str__(self):
        return f"EvaluationRun ({self.created_at} - {self.finished_at})"

    def get_absolute_url(self):
        return reverse(
            "evaluations:evaluation_results_home", args=[get_slug_for_team(self.team_id), self.config_id, self.pk]
        )

    def mark_complete(self, save=True):
        self.finished_at = timezone.now()
        self.status = EvaluationRunStatus.COMPLETED
        if save:
            self.save(update_fields=["finished_at", "status"])

    def get_table_data(self, include_ids: bool = False):
        results = self.results.select_related("message", "evaluator", "session").all()
        table_by_message = defaultdict(dict)
        for result in results:
            context_columns = {
                # exclude 'current_datetime'
                f"{key}": value
                for key, value in result.message_context.items()
                if key != "current_datetime"
            }
            if include_ids is True:
                table_by_message[result.message.id].update({"id": result.message.id})

            table_by_message[result.message.id].update(
                {
                    "Dataset Input": result.input_message,
                    "Dataset Output": result.output_message,
                    "Generated Response": result.output.get("generated_response", ""),
                    **{
                        f"{key} ({result.evaluator.name})": value
                        for key, value in result.output.get("result", {}).items()
                    },
                    **context_columns,
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
    output = SanitizedJSONField()

    def __str__(self):
        return f"EvaluatorResult for Evaluator {self.evaluator_id}"

    @property
    def input_message(self) -> str:
        try:
            return self.output["message"]["input"]["content"]
        except KeyError:
            return ""

    @property
    def output_message(self) -> str:
        try:
            return self.output["message"]["output"]["content"]
        except KeyError:
            return ""

    @property
    def message_context(self) -> dict:
        try:
            return self.output["message"]["context"]
        except KeyError:
            return {}
