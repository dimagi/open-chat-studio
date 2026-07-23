from __future__ import annotations

import importlib
from collections import defaultdict
from functools import cached_property
from typing import TYPE_CHECKING, Literal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel as PydanticBaseModel

from apps.chat.models import ChatMessage, ChatMessageType
from apps.chatbots.version_resolver import VersionSelectionRule, resolve_chatbot_version
from apps.evaluations.exceptions import InFlightRunsError
from apps.evaluations.export import build_evaluation_table_data
from apps.evaluations.rule_validation import (
    ConditionType,
    validate_condition,
    validate_field_in_schema,
    validate_tag_compatibility,
)
from apps.evaluations.utils import make_evaluation_messages_from_sessions
from apps.experiments.filters import ChatMessageFilter
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


NON_TERMINAL_RUN_STATUSES = (EvaluationRunStatus.PENDING, EvaluationRunStatus.PROCESSING)


def raise_if_runs_in_flight(runs: models.QuerySet[EvaluationRun], resource_label: str) -> None:
    """Raise InFlightRunsError if any run in `runs` is PENDING or PROCESSING.

    `runs` is an EvaluationRun queryset; `resource_label` is the user-facing
    noun for the object being deleted (e.g. "evaluation", "evaluator", "dataset").

    This only guards instance `Model.delete()` calls. Bulk `QuerySet.delete()`
    and parent-cascade deletes execute SQL-level deletes that bypass the model
    override entirely, so this is best-effort UX protection, not a hard invariant;
    the evaluation tasks are hardened to degrade to a logged no-op if a run or
    message vanishes out from under them.
    """
    if runs.filter(status__in=NON_TERMINAL_RUN_STATUSES).exists():
        raise InFlightRunsError(
            f"Cannot delete this {resource_label} while evaluation runs are in progress. "
            "Wait for the runs to finish, then try again."
        )


class EvaluationRunType(models.TextChoices):
    FULL = "full", "Full"
    PREVIEW = "preview", "Preview"
    DELTA = "delta", "Delta"


class DatasetCreationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class EvaluationMode(models.TextChoices):
    MESSAGE = "message", "Message"
    SESSION = "session", "Session"


class Evaluator(BaseTeamModel):
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=128)  # The evaluator type, should be one from evaluators.py
    params = SanitizedJSONField(
        default=dict
    )  # This is different for each evaluator. Usage is similar to how we define Nodes in pipelines
    evaluation_mode = models.CharField(
        max_length=10,
        choices=EvaluationMode.choices,
        default=EvaluationMode.MESSAGE,
        help_text="Message mode evaluates individual message pairs; Session mode evaluates entire conversations",
    )

    def __str__(self):
        try:
            label = self.evaluator.model_config["evaluator_schema"].label
        except KeyError:
            label = self.type
        return f"{self.name} ({label})"

    def delete(self, *args, **kwargs):
        """Block deletion while any config using this evaluator has an in-flight run."""
        raise_if_runs_in_flight(EvaluationRun.objects.filter(config__evaluators=self), "evaluator")
        return super().delete(*args, **kwargs)

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
    session = models.ForeignKey(
        ExperimentSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="evaluation_messages"
    )
    # null when created from CSV import or manually

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
        if not self.input and not self.output:
            return "Session evaluation"
        input_role = self.input.get("role", "(human)").title()
        input_content = self.input.get("content", "no content")
        output_role = self.output.get("role", "(ai)").title()
        output_content = self.output.get("content", "no content")
        return f"{input_role}: {input_content}, {output_role}: {output_content}"

    def delete(self, *args, **kwargs):
        """Block deletion while an in-flight run references this message, via its dataset or DELTA scoping.

        A PREVIEW/DELTA run that will not actually process this message is over-blocked here; that
        is the safe direction (never strand a run) and in-flight runs are short-lived.
        """
        related_runs = EvaluationRun.objects.filter(
            models.Q(config__dataset__messages=self) | models.Q(scoped_messages=self)
        )
        raise_if_runs_in_flight(related_runs, "message")
        return super().delete(*args, **kwargs)

    @classmethod
    def create_from_sessions(
        cls, team: Team, external_session_ids, filtered_session_ids=None, filter_params=None, timezone=None
    ) -> list[EvaluationMessage]:
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

        if filtered_session_ids and filter_params is not None:
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
            "participant_data": self.participant_data,
            "session_state": self.session_state,
        }


class EvaluationDataset(BaseTeamModel):
    name = models.CharField(max_length=255)
    evaluation_mode = models.CharField(
        max_length=10,
        choices=EvaluationMode.choices,
        default=EvaluationMode.MESSAGE,
        help_text="Message mode stores individual message pairs; Session mode stores entire conversations",
    )
    messages = models.ManyToManyField(EvaluationMessage)
    status = models.CharField(
        max_length=20,
        choices=DatasetCreationStatus.choices,
        default=DatasetCreationStatus.COMPLETED,
        help_text="Status of dataset creation",
    )
    job_id = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)

    def __str__(self):
        mode = EvaluationMode(self.evaluation_mode).label
        return f"{self.name} ({self.messages.count()} {mode}s)"

    def delete(self, *args, **kwargs):
        """Block deletion while any config using this dataset has an in-flight run."""
        raise_if_runs_in_flight(EvaluationRun.objects.filter(config__dataset=self), "dataset")
        return super().delete(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("evaluations:dataset_edit", args=[get_slug_for_team(self.team_id), self.id])

    @property
    def is_processing(self):
        return self.status == DatasetCreationStatus.PROCESSING

    @property
    def is_failed(self):
        return self.status == DatasetCreationStatus.FAILED

    @property
    def is_complete(self):
        return self.status == DatasetCreationStatus.COMPLETED

    @property
    def is_pending(self):
        return self.status == DatasetCreationStatus.PENDING

    def add_messages(self, messages: list[EvaluationMessage]) -> tuple[list[EvaluationMessage], int]:
        """Persist and link messages to this dataset, skipping duplicate references.

        Deduplication is mode-aware:
        - message mode: a message duplicates another when they share the same
          (input_chat_message_id, expected_output_chat_message_id) pair.
        - session mode: a message duplicates another when they share the same session_id.

        Messages without the relevant references (e.g. manual or CSV rows with null
        FKs) are never treated as duplicates. The dataset row is locked for the
        duration to serialise concurrent adds. Returns (created_messages, skipped_count).
        """
        with transaction.atomic():
            # Lock the dataset row so concurrent add_messages calls can't both
            # read "not a duplicate" and each insert the same reference.
            EvaluationDataset.objects.select_for_update().get(pk=self.pk)

            seen = self._existing_dedup_keys()
            to_create = []
            skipped = 0
            for message in messages:
                key = self._dedup_key(message)
                if key is not None and key in seen:
                    skipped += 1
                    continue
                if key is not None:
                    seen.add(key)
                to_create.append(message)

            created = []
            if to_create:
                created = EvaluationMessage.objects.bulk_create(to_create)
                self.messages.add(*created)
            return created, skipped

    def _dedup_key(self, message: EvaluationMessage) -> tuple | None:
        """Return the dedup key for a message, or None if it can't be a duplicate."""
        if self.evaluation_mode == EvaluationMode.SESSION:
            if message.session_id is None:
                return None
            return ("session", message.session_id)
        if message.input_chat_message_id is None or message.expected_output_chat_message_id is None:
            return None
        return ("pair", message.input_chat_message_id, message.expected_output_chat_message_id)

    def _existing_dedup_keys(self) -> set[tuple]:
        """Build the set of dedup keys already present in this dataset."""
        if self.evaluation_mode == EvaluationMode.SESSION:
            session_ids = self.messages.filter(session__isnull=False).values_list("session_id", flat=True)
            return {("session", session_id) for session_id in session_ids}
        pairs = self.messages.filter(
            input_chat_message_id__isnull=False,
            expected_output_chat_message_id__isnull=False,
        ).values_list("input_chat_message_id", "expected_output_chat_message_id")
        return {("pair", input_id, output_id) for input_id, output_id in pairs}

    class Meta:
        unique_together = ("name", "team")


class AutoPopulationRunStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    ERROR = "error", "Error"
    NO_OP = "no_op", "No-op"


class DatasetAutoPopulationRule(BaseTeamModel):
    """A continuous-ingestion rule that pulls new sessions from a source experiment
    into an evaluation dataset on each polling tick."""

    AUTO_DISABLE_FAILURE_THRESHOLD = 3

    dataset = models.ForeignKey(
        EvaluationDataset,
        on_delete=models.CASCADE,
        related_name="auto_population_rules",
    )
    source_experiment = models.ForeignKey(
        "experiments.Experiment",
        on_delete=models.CASCADE,
        related_name="auto_population_rules",
        help_text="Sessions from this chatbot are considered for auto-population.",
    )
    filter_query_string = models.TextField(
        blank=True,
        help_text=(
            "Filter criteria as a query string; empty means 'all sessions from this bot'. "
            "Format matches FilterParams used elsewhere."
        ),
    )
    is_enabled = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run_status = models.CharField(max_length=10, choices=AutoPopulationRunStatus.choices, blank=True)
    last_error = models.TextField(blank=True)
    consecutive_failure_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["is_enabled", "last_run_at"])]

    def __str__(self) -> str:
        return f"AutoPopRule({self.source_experiment_id} -> dataset {self.dataset_id})"

    def clean(self):
        super().clean()
        if self.team_id and self.dataset_id and self.dataset.team_id != self.team_id:
            raise ValidationError({"dataset": "Dataset must belong to the same team as the rule."})
        if self.dataset_id and self.dataset.evaluation_mode != EvaluationMode.SESSION:
            raise ValidationError({"dataset": "Auto-population rules are only supported for session-level datasets."})
        if self.team_id and self.source_experiment_id and self.source_experiment.team_id != self.team_id:
            raise ValidationError({"source_experiment": "Source chatbot must belong to the same team as the rule."})

    def get_absolute_url(self):
        return reverse(
            "evaluations:auto_population_rule_edit",
            args=[get_slug_for_team(self.team_id), self.dataset_id, self.id],
        )


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
        choices=VersionSelectionRule.choices,
        default=VersionSelectionRule.SPECIFIC,
        help_text=("Type of version selection: specific, latest_working, or latest_published"),
    )
    auto_run_on_append = models.BooleanField(
        default=False,
        help_text=(
            "When enabled, every time the dataset receives newly auto-populated rows "
            "this evaluation runs automatically over only those rows. May incur LLM cost."
        ),
    )

    def __str__(self):
        return f"EvaluationConfig ({self.name})"

    def delete(self, *args, **kwargs):
        """Block deletion while any of this config's runs is still in progress."""
        raise_if_runs_in_flight(EvaluationRun.objects.filter(config=self), "evaluation")
        return super().delete(*args, **kwargs)

    def get_generation_experiment_version(self):
        """Resolve the actual experiment version based on selection type.

        SPECIFIC short-circuits to the stored FK (no family-and-number round-trip).
        Other rules delegate to the resolver. Returns None when the config is
        incompletely configured; the resolver raises on programmer errors.
        """
        if self.version_selection_type == VersionSelectionRule.SPECIFIC:
            return self.experiment_version
        if self.base_experiment_id is None:
            return None
        return resolve_chatbot_version(
            self.base_experiment,
            self.version_selection_type,
        )

    def get_absolute_url(self):
        return reverse("evaluations:evaluation_runs_home", args=[get_slug_for_team(self.team_id), self.id])

    def run(
        self,
        run_type: EvaluationRunType = EvaluationRunType.FULL,
        scoped_messages: list[EvaluationMessage] | None = None,
    ) -> EvaluationRun:
        """Runs the evaluation asynchronously using Celery.

        When `scoped_messages` is provided, the run only evaluates those
        messages instead of the dataset's full membership.
        """
        generation_experiment = self.get_generation_experiment_version()
        run = EvaluationRun.objects.create(
            team=self.team,
            config=self,
            generation_experiment=generation_experiment,
            status=EvaluationRunStatus.PENDING,
            type=run_type,
        )
        if scoped_messages is not None:
            run.scoped_messages.add(*scoped_messages)

        from apps.evaluations.tasks import (  # noqa: PLC0415 - circular: evaluations.tasks imports evaluations.models
            run_evaluation_task,
        )

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
    scoped_messages = models.ManyToManyField(
        EvaluationMessage,
        blank=True,
        related_name="scoping_runs",
        help_text=(
            "The frozen message plan for this run (all dataset ids for FULL, the sample for PREVIEW, "
            "the explicit list for DELTA)."
        ),
    )
    # Coordination state, written only by the beat coordinator under a row lock.
    evaluator_ids = SanitizedJSONField(default=list)  # evaluator ids frozen at creation
    in_flight = SanitizedJSONField(default=list)  # message ids of the current wave
    wave_dispatched_at = models.DateTimeField(null=True, blank=True)
    stall_count = models.PositiveSmallIntegerField(default=0)
    taskbadger_task_id = models.CharField(max_length=255, blank=True)

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
        results_qs = (
            self.results.select_related("message__session__experiment", "evaluator", "session")
            .prefetch_related("applied_tags__tag")
            .order_by("created_at")
        )
        if self.type == EvaluationRunType.DELTA and self.scoped_messages.exists():
            scoped_ids = self.scoped_messages.values_list("id", flat=True)
            results_qs = results_qs.filter(message_id__in=scoped_ids)

        return build_evaluation_table_data(results_qs, include_ids=include_ids)


class EvaluationResult(BaseTeamModel):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.CASCADE)
    message = models.ForeignKey(EvaluationMessage, on_delete=models.CASCADE)
    run = models.ForeignKey(EvaluationRun, on_delete=models.CASCADE, related_name="results")
    session = models.ForeignKey(ExperimentSession, on_delete=models.SET_NULL, null=True)
    output = SanitizedJSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run", "message", "evaluator"],
                name="unique_result_per_run_message_evaluator",
            ),
        ]

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


class EvaluationRunAggregate(BaseModel):
    """Stores aggregated results for an evaluation run, per evaluator."""

    run = models.ForeignKey(EvaluationRun, on_delete=models.CASCADE, related_name="aggregates")
    evaluator = models.ForeignKey(Evaluator, on_delete=models.CASCADE)
    aggregates = models.JSONField(default=dict)
    computed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("run", "evaluator")


class EvaluatorTagRule(BaseTeamModel):
    """A rule that applies a tag to the eval target when an evaluator output field matches a condition."""

    evaluator = models.ForeignKey(Evaluator, on_delete=models.CASCADE, related_name="tag_rules")
    tag = models.ForeignKey("annotations.Tag", on_delete=models.PROTECT, related_name="evaluator_tag_rules")
    field_name = models.CharField(max_length=255)
    condition_type = models.CharField(max_length=20, choices=ConditionType.choices)
    condition_value = models.JSONField(default=dict)

    def __str__(self):
        return f"{self.evaluator.name}: {self.field_name} {self.condition_type} {self.condition_value}"

    def clean(self):
        super().clean()

        if self.evaluator_id and self.team_id and self.evaluator.team_id != self.team_id:
            raise ValidationError({"team": "Rule team must match evaluator team."})

        if self.tag_id and self.evaluator_id:
            validate_tag_compatibility(self.tag, self.evaluator)

        if self.evaluator_id:
            output_schema = self.evaluator.params.get("output_schema", {}) or {}
            field_def = validate_field_in_schema(self.field_name, output_schema)
            validate_condition(self.condition_type, self.condition_value, field_def)


class AppliedTag(BaseTeamModel):
    """Audit row recording that a specific rule applied a tag against a specific evaluation result."""

    evaluation_result = models.ForeignKey(EvaluationResult, on_delete=models.CASCADE, related_name="applied_tags")
    rule = models.ForeignKey(EvaluatorTagRule, on_delete=models.CASCADE, related_name="applications")
    tag = models.ForeignKey("annotations.Tag", on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["evaluation_result", "rule", "tag"],
                name="unique_applied_tag_per_result_rule",
            ),
        ]
        indexes = [
            models.Index(fields=["rule"]),
        ]

    def __str__(self):
        return f"AppliedTag(result={self.evaluation_result_id}, rule={self.rule_id}, tag={self.tag_id})"
