import contextlib
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from io import StringIO

import taskbadger
from celery import current_app, shared_task
from celery.utils.log import get_task_logger
from celery_progress.backend import PROGRESS_STATE, ProgressRecorder
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Prefetch
from django.http import QueryDict
from django.utils import timezone
from taskbadger import StatusEnum
from taskbadger.celery import Task as TaskbadgerTask

from apps.assessments.score_writers import write_scores_from_evaluation_result
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.tasks import handle_evaluation_message
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.evaluations.aggregation import compute_aggregates_for_run
from apps.evaluations.auto_population import (
    auto_populate_eval_datasets,  # noqa: F401 -- imported so Celery autodiscovery registers the task
)
from apps.evaluations.exceptions import HistoryParseException
from apps.evaluations.export import build_evaluation_table_data, write_evaluation_csv
from apps.evaluations.models import (
    NON_TERMINAL_RUN_STATUSES,
    DatasetCreationStatus,
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationMessageContent,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationRunType,
    Evaluator,
    EvaluatorTagRule,
)
from apps.evaluations.tagging import apply_rules_to_result, reverse_stale_tags
from apps.evaluations.utils import make_session_evaluation_messages, parse_csv_value_as_json, parse_history_text
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.files.models import File, FilePurpose
from apps.teams.utils import current_team
from apps.web.dynamic_filters.datastructures import FilterParams

EVAL_SESSIONS_TTL_DAYS = 30

# --- Beat-coordinated evaluation batches (see docs/adr/0046-deploy-safe-evaluation-runs.md) ---
BATCH_SIZE = 3  # messages per batch task
BATCHES_PER_TICK = 10  # batch tasks dispatched per tick; BATCHES_PER_TICK * BATCH_SIZE = messages per tick
STALL_TIMEOUT = timedelta(minutes=12)  # no fresh results for this long => the dispatched batch is stalled
MAX_STALLS = 3  # consecutive stalls with no progress => run marked FAILED
BATCH_SOFT_TIME_LIMIT = 240  # seconds; best-effort bound under the 5-min visibility timeout
TASKBADGER_STALE_TIMEOUT = 300  # seconds; TB alerts if a run's task goes this long without an update

logger = get_task_logger("ocs.evaluations")


def _save_dataset_error(dataset: EvaluationDataset, error_message: str):
    """Helper to save dataset error status and clear job_id."""
    dataset.status = DatasetCreationStatus.FAILED
    dataset.error_message = error_message
    dataset.job_id = ""
    dataset.save(update_fields=["status", "error_message", "job_id"])


def _pending_evaluator_ids(evaluation_run, message_id, evaluator_ids):
    """Return the subset of evaluator_ids with no EvaluationResult yet for (run, message).

    Error results ({"error": ...}) count as done, matching the current no-retry behaviour.
    """
    already_done = set(
        EvaluationResult.objects.filter(
            run=evaluation_run, message_id=message_id, evaluator_id__in=evaluator_ids
        ).values_list("evaluator_id", flat=True)
    )
    return [evaluator_id for evaluator_id in evaluator_ids if evaluator_id not in already_done]


def _create_evaluation_result(evaluation_run, evaluator, message, output, session_id, *, apply_tags):
    """Create one EvaluationResult idempotently; absorb a duplicate-race insert.

    Returns the created result, or None when a concurrent delivery already wrote the
    row (the unique constraint rejects the second insert — the other delivery won).

    Tag application deliberately shares the guarded atomic block so the result and its
    tags commit together. Today _maybe_apply_tag_rules cannot raise IntegrityError (its
    tag writes use bulk_create with ignore_conflicts=True and an AppliedTag unique key
    that includes the brand-new result row). If a future tag write could raise it, this
    guard would misreport a fresh-result loss as a duplicate-race skip and would need
    narrowing to the create() alone.
    """
    try:
        with transaction.atomic():
            evaluation_result = EvaluationResult.objects.create(
                message=message,
                run=evaluation_run,
                evaluator=evaluator,
                output=output,
                team=evaluation_run.team,
                session_id=session_id,
            )
            if apply_tags:
                _maybe_apply_tag_rules(evaluation_run, evaluator, evaluation_result, message)
    except IntegrityError:
        logger.info(
            "EvaluationResult for run %s message %s evaluator %s already exists; skipping (race)",
            evaluation_run.id,
            message.id,
            evaluator.id,
        )
        return None
    return evaluation_result


def _run_evaluator_on_message(evaluation_run, evaluator, message, bot_response, session_id):
    """Run a single evaluator over a single message and persist the outcome.

    On evaluator failure an error result is stored (matching the no-retry behaviour);
    on success the result is written and its Score rows are derived.
    """
    try:
        output = evaluator.run(message, bot_response or "").model_dump()
    except Exception as e:
        logger.exception(f"Error running evaluator {evaluator.id} on message {message.id}: {e}")
        _create_evaluation_result(evaluation_run, evaluator, message, {"error": str(e)}, session_id, apply_tags=False)
        return

    evaluation_result = _create_evaluation_result(
        evaluation_run, evaluator, message, output, session_id, apply_tags=True
    )
    if evaluation_result is None:
        return
    try:
        write_scores_from_evaluation_result(evaluation_result)
    except Exception:
        logger.exception("Failed to write Score rows for EvaluationResult %s", evaluation_result.id)


@shared_task
def evaluate_single_message_task(evaluation_run_id, evaluator_ids, message_id):
    """
    Run the outstanding evaluations over a single message, in-process.

    Idempotent by design: broker redelivery and stall re-dispatch deliberately
    re-run this task, so it first drops evaluators that already have a result for
    (run, message) and returns before bot generation if none remain. A partially
    evaluated message re-runs bot generation, so remaining evaluators judge a fresh
    bot response (rare crash-path artifact). Duplicate inserts that race past the
    skip check are absorbed by the unique constraint (the other delivery won).
    ExperimentSessions created here are deleted periodically by cleanup_old_evaluation_data.
    """
    try:
        evaluation_run = EvaluationRun.objects.select_related("team").get(id=evaluation_run_id)
    except EvaluationRun.DoesNotExist:
        logger.warning("EvaluationRun %s no longer exists; skipping message %s", evaluation_run_id, message_id)
        return

    with current_team(evaluation_run.team):
        try:
            message = EvaluationMessage.objects.select_related("session__chat", "expected_output_chat_message").get(
                id=message_id
            )
        except EvaluationMessage.DoesNotExist:
            logger.warning("EvaluationMessage %s no longer exists; skipping in run %s", message_id, evaluation_run_id)
            return

        pending_evaluator_ids = _pending_evaluator_ids(evaluation_run, message_id, evaluator_ids)
        if not pending_evaluator_ids:
            return

        # Only run bot generation if an experiment version is configured
        generation_experiment = evaluation_run.generation_experiment
        session_id, bot_response = None, ""
        if generation_experiment is not None:
            session_id, bot_response = run_bot_generation(evaluation_run.team, message, generation_experiment)

        evaluators_qs = Evaluator.objects.filter(id__in=pending_evaluator_ids).prefetch_related(
            Prefetch("tag_rules", queryset=EvaluatorTagRule.objects.select_related("tag")),
        )
        evaluators = {e.id: e for e in evaluators_qs}
        for evaluator_id in pending_evaluator_ids:
            evaluator = evaluators.get(evaluator_id)
            if evaluator is None:
                logger.warning(f"Evaluator {evaluator_id} not found, skipping")
                continue
            _run_evaluator_on_message(evaluation_run, evaluator, message, bot_response, session_id)


@shared_task(acks_late=True, soft_time_limit=BATCH_SOFT_TIME_LIMIT)
def evaluate_message_batch(evaluation_run_id, message_ids):
    """Evaluate a small batch of messages in-process, then exit.

    Deliberately dumb: no refill, no completion check, no self-rescheduling — all
    coordination lives in the beat sweep. acks_late means a worker killed mid-batch
    has the batch redelivered by Redis after the visibility timeout; the per-message
    idempotency check resumes where it died.
    """
    try:
        run = EvaluationRun.objects.get(id=evaluation_run_id)
    except EvaluationRun.DoesNotExist:
        logger.warning("EvaluationRun %s gone; dropping batch of %s messages", evaluation_run_id, len(message_ids))
        return

    if run.status != EvaluationRunStatus.PROCESSING:
        logger.info("EvaluationRun %s no longer processing (%s); dropping batch", evaluation_run_id, run.status)
        return

    for message_id in message_ids:
        evaluate_single_message_task(evaluation_run_id, run.evaluator_ids, message_id)


@dataclass
class _TickResult:
    """What a single coordination tick decided; consumed after the transaction commits."""

    batches: list[list[int]]
    done: int
    total: int
    terminal: str | None  # None | "success" | "error"


def _chunk(ids, size):
    return [ids[i : i + size] for i in range(0, len(ids), size)]


def _done_message_ids(run, plan_ids, evaluator_ids) -> set[int]:
    """Message ids in the plan that have a result from every evaluator in the plan."""
    evaluator_count = len(set(evaluator_ids))
    if evaluator_count == 0:
        return set()
    rows = (
        EvaluationResult.objects.filter(run=run, message_id__in=plan_ids, evaluator_id__in=evaluator_ids)
        .values("message_id")
        .annotate(cnt=Count("evaluator_id", distinct=True))
        .filter(cnt=evaluator_count)
        .values_list("message_id", flat=True)
    )
    return set(rows)


def _is_stalled(run) -> bool:
    """True if no fresh results have landed for the current batch within STALL_TIMEOUT.

    The batch_dispatched_at floor stops a freshly dispatched batch (zero results yet)
    from looking stalled.
    """
    newest = EvaluationResult.objects.filter(run=run).aggregate(m=Max("created_at"))["m"]
    reference = max([ts for ts in (newest, run.batch_dispatched_at) if ts is not None], default=None)
    if reference is None:
        return False
    return timezone.now() - reference > STALL_TIMEOUT


def _dispatch_new_batch(run, ordered_remaining) -> list[list[int]]:
    """Pick the next batch of work, persist coordination state, return the batch tasks to dispatch."""
    dispatched = ordered_remaining[: BATCHES_PER_TICK * BATCH_SIZE]
    run.in_flight = dispatched
    run.batch_dispatched_at = timezone.now()
    run.stall_count = 0  # reaching here means progress (first batch, or previous batch done)
    run.status = EvaluationRunStatus.PROCESSING
    run.save(update_fields=["in_flight", "batch_dispatched_at", "stall_count", "status"])
    return _chunk(dispatched, BATCH_SIZE)


def _redispatch_unfinished(run, remaining) -> tuple[list[list[int]], bool]:
    """Re-dispatch only the unfinished messages of the current batch.

    Returns (batches, failed). `failed` is True when the run has stalled MAX_STALLS
    times without any progress and has been marked FAILED.
    """
    in_flight = run.in_flight or []
    unfinished = [message_id for message_id in in_flight if message_id in remaining]
    made_progress = len(unfinished) < len(in_flight)

    if made_progress:
        run.stall_count = 1
    else:
        run.stall_count = (run.stall_count or 0) + 1

    if run.stall_count >= MAX_STALLS and not made_progress:
        run.status = EvaluationRunStatus.FAILED
        run.error_message = (
            f"Evaluation stalled: {len(unfinished)} message(s) made no progress after "
            f"{MAX_STALLS} re-dispatch attempts."
        )
        run.save(update_fields=["status", "error_message", "stall_count"])
        return [], True

    run.in_flight = unfinished
    run.batch_dispatched_at = timezone.now()  # reset the clock to avoid a hot re-dispatch loop
    run.save(update_fields=["in_flight", "batch_dispatched_at", "stall_count"])
    return _chunk(unfinished, BATCH_SIZE), False


def _finalize_complete(run) -> None:
    """Mark the run complete and run the completion side effects (aggregates, tag reversal)."""
    run.mark_complete()
    compute_aggregates_for_run(run)
    reverse_stale_tags(run)


def _coordinate_locked_run(run) -> _TickResult:
    """Run one coordination tick for a locked, non-terminal run.

    Mutates and saves the run's coordination state (or completes/fails it) and returns
    the batches to dispatch plus the progress numbers to publish AFTER the surrounding
    transaction commits. Never dispatches or publishes itself.
    """
    plan_ids = list(run.scoped_messages.values_list("id", flat=True))
    total = len(plan_ids)
    evaluator_ids = run.evaluator_ids or []

    if total == 0 or not evaluator_ids:
        _finalize_complete(run)
        return _TickResult(batches=[], done=0, total=total, terminal="success")

    remaining = set(plan_ids) - _done_message_ids(run, plan_ids, evaluator_ids)
    done = total - len(remaining)

    if not remaining:
        _finalize_complete(run)
        return _TickResult(batches=[], done=total, total=total, terminal="success")

    in_flight = set(run.in_flight or [])
    batch_done = not (in_flight & remaining)

    if run.status == EvaluationRunStatus.PENDING or batch_done:
        batches = _dispatch_new_batch(run, sorted(remaining))
        return _TickResult(batches=batches, done=done, total=total, terminal=None)

    if _is_stalled(run):
        batches, failed = _redispatch_unfinished(run, remaining)
        return _TickResult(batches=batches, done=done, total=total, terminal="error" if failed else None)

    # dispatched batch in progress with fresh results — the common case — costs nothing.
    return _TickResult(batches=[], done=done, total=total, terminal=None)


def _drive_run(run_id) -> None:
    """Coordinate a single run under a row lock, then dispatch + publish after commit.

    Taskbadger task creation and progress publishing both run AFTER the transaction
    commits, so the blocking Taskbadger HTTP call never holds the row lock and a
    rolled-back tick can't strand an orphaned remote task keyed to a discarded id.
    """
    with transaction.atomic():
        run = (
            EvaluationRun.objects.select_for_update(skip_locked=True)
            .filter(id=run_id, status__in=NON_TERMINAL_RUN_STATUSES)
            .first()
        )
        if run is None:
            return  # locked by another driver, or already terminal/gone
        with current_team(run.team):
            result = _coordinate_locked_run(run)

    with current_team(run.team):
        for batch in result.batches:
            evaluate_message_batch.delay(run.id, batch)
        _ensure_taskbadger_task(run, result.total)
        _publish_tick(run, result)


@shared_task
def coordinate_evaluation_runs():
    """Beat task (every 30s): drive every active evaluation run one tick.

    Stateless between ticks — any work lost to a deploy is recomputed and repaired
    on the next tick. Overlapping sweeps partition runs via select_for_update(skip_locked).
    """
    run_ids = list(
        EvaluationRun.objects.filter(status__in=NON_TERMINAL_RUN_STATUSES)
        .order_by("created_at")
        .values_list("id", flat=True)
    )
    for run_id in run_ids:
        try:
            _drive_run(run_id)
        except Exception:
            logger.exception("Coordination tick failed for evaluation run %s", run_id)


def _publish_progress(job_id, current, total, *, stop=False) -> None:
    """Publish run progress to the Celery result backend under `job_id`.

    The frontend polls celery_progress:task_status for this id. `stop=True` writes a
    SUCCESS state so the poller stops and reloads the page (used on completion and on
    terminal failure — the reloaded page reveals the real COMPLETED/FAILED status).
    """
    if not job_id:
        return
    percent = float(round((current / total) * 100, 2)) if total else 100.0
    meta = {
        "pending": False,
        "current": current,
        "total": total,
        "percent": percent,
        "description": f"{current} of {total} evaluated",
    }
    state = "SUCCESS" if stop else PROGRESS_STATE
    try:
        current_app.backend.store_result(job_id, meta, state)
    except Exception:
        logger.exception("Failed to publish evaluation progress for %s", job_id)


def _ensure_taskbadger_task(run, total) -> None:
    """Create the run's Taskbadger task once, after the tick's transaction has committed.

    Called outside the coordination lock so the blocking HTTP request never stalls
    other runs in the sweep. The taskbadger_task_id check keeps it effectively
    once-per-run; a rare duplicate under overlapping sweeps is harmless for monitoring.
    """
    if run.taskbadger_task_id:
        return
    task = taskbadger.create_task_safe(
        name=f"Evaluation run {run.id}",
        status=StatusEnum.PROCESSING,
        value=0,
        value_max=total,
        stale_timeout=TASKBADGER_STALE_TIMEOUT,
    )
    if task is not None:
        run.taskbadger_task_id = task.id
        run.save(update_fields=["taskbadger_task_id"])


def _update_taskbadger(run, *, value, value_max, status=None) -> None:
    if not run.taskbadger_task_id:
        return
    kwargs = {"value": value, "value_max": value_max}
    if status is not None:
        kwargs["status"] = status
    taskbadger.update_task_safe(run.taskbadger_task_id, **kwargs)


def _publish_tick(run, result: "_TickResult") -> None:
    """After-commit side effects for one tick: progress publish + Taskbadger update."""
    if result.terminal == "success":
        _publish_progress(run.job_id, result.total, result.total, stop=True)
        _update_taskbadger(run, value=result.total, value_max=result.total, status=StatusEnum.SUCCESS)
    elif result.terminal == "error":
        _publish_progress(run.job_id, result.done, result.total, stop=True)
        _update_taskbadger(run, value=result.done, value_max=result.total, status=StatusEnum.ERROR)
    else:
        _publish_progress(run.job_id, result.done, result.total)
        _update_taskbadger(run, value=result.done, value_max=result.total)


def _mark_run_failed(run_id, message) -> None:
    run = EvaluationRun.objects.filter(id=run_id).first()
    if run is None:
        logger.warning("EvaluationRun %s vanished before it could be marked FAILED", run_id)
        return
    run.status = EvaluationRunStatus.FAILED
    run.error_message = message
    run.save(update_fields=["status", "error_message"])
    _publish_progress(run.job_id, 0, 0, stop=True)
    _update_taskbadger(run, value=0, value_max=0, status=StatusEnum.ERROR)


def _maybe_apply_tag_rules(
    evaluation_run: EvaluationRun,
    evaluator: Evaluator,
    evaluation_result: EvaluationResult,
    message: EvaluationMessage,
) -> None:
    """Skip tagging on preview runs or results with errors; otherwise apply rules."""
    if evaluation_run.type == EvaluationRunType.PREVIEW:
        return
    if (evaluation_result.output or {}).get("error"):
        return
    apply_rules_to_result(evaluation_result, evaluator, message)


def run_bot_generation(team, message: EvaluationMessage, experiment: Experiment) -> tuple[int | None, str | None]:
    """
    Run the evaluation message through the bot to generate a response.
    """
    try:
        # TODO: Do we get the participant from the EvaluationMessage?
        participant, _ = Participant.objects.get_or_create(
            identifier="evaluations",
            team=team,
            platform=ChannelPlatform.EVALUATIONS,
            defaults={"name": "Evaluations Bot"},
        )
        evaluation_channel = ExperimentChannel.objects.get_team_evaluations_channel(team)

        chat = Chat.objects.create(team=team)
        session = ExperimentSession.objects.create(
            team=team,
            experiment=experiment,
            participant=participant,
            experiment_channel=evaluation_channel,
            chat=chat,
            state=message.session_state,
            platform=evaluation_channel.platform,
        )

        # Populate history on the chat with the history from the EvaluationMessage
        if message.history:
            _create_message_history(chat, message.history)

    except Exception as e:
        logger.exception(f"Error populating eval data {message.id}: {e}")
        # Don't fail the entire evaluation if bot generation fails
        return None, None

    try:
        # Extract the input message content
        input_content = message.input.get("content", "")

        participant_data = message.participant_data | {}
        participant_data = session.participant.global_data | participant_data

        # Call the bot with the evaluation message and session
        bot_response = handle_evaluation_message(
            experiment_version=experiment,
            experiment_channel=evaluation_channel,
            message_text=input_content,
            session=session,
            participant_data=participant_data,
        )
        response_content = bot_response.content
        logger.debug(f"Bot generated response for evaluation message {message.id}: {response_content}")

        return session.id, response_content

    except Exception as e:
        logger.exception(f"Error generating bot response for evaluation message {message.id}: {e}")
        # Don't fail the entire evaluation if bot generation fails
        return session.id, None


def _create_message_history(chat, history: list[dict]):
    # Set explicit timestamps with incremental offsets to ensure proper chronological ordering
    # when messages are retrieved with order_by("created_at")
    base_time = timezone.now() - timedelta(seconds=len(history))
    history_messages = [
        ChatMessage(
            chat=chat,
            message_type=history_entry.get("message_type", ChatMessageType.HUMAN),
            content=history_entry.get("content", ""),
            summary=history_entry.get("summary"),
            created_at=base_time + timedelta(seconds=idx),
        )
        for idx, history_entry in enumerate(history)
    ]
    ChatMessage.objects.bulk_create(history_messages)


@shared_task
def run_evaluation_task(evaluation_run_id):
    """Fast-path kick for a freshly created run: run one coordination tick immediately
    so the first batch dispatches without waiting for the next beat. The beat sweep's
    PENDING branch is the backstop if this dispatcher is lost.
    """
    try:
        _drive_run(evaluation_run_id)
    except Exception as e:
        logger.exception(f"Error starting evaluation run {evaluation_run_id}: {e}")
        _mark_run_failed(evaluation_run_id, str(e))


@shared_task(base=TaskbadgerTask)
def cleanup_old_evaluation_data():
    """Delete ExperimentSessions that were created during evaluation runs and
    are older than one week.

    """
    one_week_ago = timezone.now() - timedelta(days=EVAL_SESSIONS_TTL_DAYS)
    old_evaluation_sessions = ExperimentSession.objects.filter(
        experiment_channel__platform=ChannelPlatform.EVALUATIONS, created_at__lt=one_week_ago
    )

    sessions_count = old_evaluation_sessions.count()
    if sessions_count == 0:
        logger.info("No old evaluation sessions found to cleanup")
        return
    # Delete via Chat rather than ExperimentSession so the cascade also removes
    # ChatMessage records. ExperimentSession.chat is a OneToOneField with
    # on_delete=CASCADE, so deleting the Chat cascades to the session as well.
    deleted_chats = Chat.objects.filter(experiment_session__in=old_evaluation_sessions).delete()

    logger.info(f"Cleanup completed: deleted {deleted_chats[0]} chat records and associated evaluation sessions")


@shared_task(base=TaskbadgerTask)
def cleanup_old_preview_evaluation_runs():
    """Delete preview evaluation runs older than 1 day"""
    one_day_ago = timezone.now() - timedelta(days=1)
    old_preview_runs = EvaluationRun.objects.filter(type=EvaluationRunType.PREVIEW, created_at__lt=one_day_ago)

    preview_runs_count = old_preview_runs.count()
    if preview_runs_count == 0:
        logger.info("No old preview evaluation runs found to cleanup")
        return

    deleted_preview_runs = old_preview_runs.delete()
    logger.info(f"Cleanup completed: deleted {deleted_preview_runs[0]} preview evaluation runs")


@shared_task(bind=True, base=TaskbadgerTask)
def update_dataset_from_csv_task(self, dataset_id, file_id, team_id):
    """
    Process CSV upload for dataset asynchronously with progress tracking.

    Args:
        dataset_id: ID of the EvaluationDataset to update
        file_id: ID of the File instance containing the CSV data
        team_id: ID of the team
    """
    progress_recorder = ProgressRecorder(self)

    try:
        dataset = EvaluationDataset.objects.select_related("team").get(id=dataset_id, team_id=team_id)
        team = dataset.team

        csv_file = File.objects.get(id=file_id, team_id=team_id)

        try:
            csv_content = csv_file.file.read().decode("utf-8")
            with current_team(team):
                rows, columns = _parse_csv_content(csv_content, progress_recorder)
                if not rows:
                    return {"success": False, "error": "CSV file is empty"}

                stats = process_csv_rows(dataset, rows, columns, progress_recorder, team)
                progress_recorder.set_progress(100, 100, "Upload complete")

                return {
                    "success": True,
                    "updated_count": stats["updated_count"],
                    "created_count": stats["created_count"],
                    "total_processed": stats["updated_count"] + stats["created_count"],
                    "errors": stats["error_messages"],
                }
        finally:
            csv_file.delete()
    except Exception as e:
        logger.error(f"Error in CSV upload task for dataset {dataset_id}: {str(e)}")
        return {"success": False, "error": str(e)}


@shared_task(bind=True, base=TaskbadgerTask)
def create_dataset_from_csv_task(
    self, dataset_id, file_id, team_id, column_mapping, history_column=None, populate_history=False
):
    """
    Create dataset messages from CSV with column mapping asynchronously.

    Args:
        dataset_id: ID of the EvaluationDataset to populate
        file_id: ID of the File instance containing the CSV data
        team_id: ID of the team
        column_mapping: Dictionary mapping CSV columns to message fields
        history_column: Optional column name containing history data
        populate_history: Whether to auto-populate history from previous messages
    """
    progress_recorder = ProgressRecorder(self)
    dataset = None

    try:
        dataset = EvaluationDataset.objects.select_related("team").get(id=dataset_id, team_id=team_id)
    except EvaluationDataset.DoesNotExist:
        logger.error(f"Dataset {dataset_id} not found for team {team_id}")
        return {"success": False, "error": "Dataset not found"}

    team = dataset.team
    dataset.status = DatasetCreationStatus.PROCESSING
    dataset.save(update_fields=["status"])

    try:
        csv_file = File.objects.get(id=file_id, team_id=team_id)
    except File.DoesNotExist:
        logger.error(f"CSV file {file_id} not found for team {team_id}")
        _save_dataset_error(dataset, "CSV file not found")
        return {"success": False, "error": "CSV file not found"}

    try:
        try:
            csv_content = csv_file.file.read().decode("utf-8")
            csv_reader = csv.DictReader(StringIO(csv_content))
        except UnicodeDecodeError as e:
            logger.error(f"Failed to decode CSV file {file_id}: {e}")
            message = "Failed to decode CSV file"
            _save_dataset_error(dataset, message)
            return {"success": False, "error": message}
        except csv.Error as e:
            logger.error(f"Failed to parse CSV file {file_id}: {e}")
            message = "Failed to parse CSV file. Please ensure it's properly formatted."
            _save_dataset_error(dataset, message)
            return {"success": False, "error": message}

        progress_recorder.set_progress(5, 100, "Parsing CSV...")

        evaluation_messages = []
        auto_history = []
        row_count = 0

        with current_team(team):
            for row in csv_reader:
                row_count += 1

                # Extract mapped columns
                input_content = row.get(column_mapping.get("input", ""), "").strip()
                output_content = row.get(column_mapping.get("output", ""), "").strip()
                if not input_content or not output_content:
                    continue

                context = {}
                if context_mapping := column_mapping.get("context"):
                    for field_name, csv_column in context_mapping.items():
                        if csv_column in row:
                            context[field_name] = parse_csv_value_as_json(row[csv_column])

                participant_data = {}
                if participant_data_mapping := column_mapping.get("participant_data"):
                    for field_name, csv_column in participant_data_mapping.items():
                        if csv_column in row:
                            participant_data[field_name] = parse_csv_value_as_json(row[csv_column])

                session_state = {}
                if session_state_mapping := column_mapping.get("session_state"):
                    for field_name, csv_column in session_state_mapping.items():
                        if csv_column in row:
                            session_state[field_name] = parse_csv_value_as_json(row[csv_column])

                message_history = []
                if populate_history:
                    # Use auto-populated history from previous messages
                    message_history = [msg.copy() for msg in auto_history]
                elif history_column and history_column in row:
                    # Parse history from CSV column
                    history_text = row[history_column].strip()
                    if history_text:
                        message_history = parse_history_text(history_text)

                evaluation_messages.append(
                    EvaluationMessage(
                        input=EvaluationMessageContent(content=input_content, role="human").model_dump(),
                        output=EvaluationMessageContent(content=output_content, role="ai").model_dump(),
                        context=context,
                        participant_data=participant_data,
                        session_state=session_state,
                        history=message_history,
                        metadata={"created_mode": "csv"},
                    )
                )

                if populate_history:
                    auto_history.append(
                        {
                            "message_type": ChatMessageType.HUMAN,
                            "content": input_content.strip(),
                            "summary": None,
                        }
                    )
                    auto_history.append(
                        {
                            "message_type": ChatMessageType.AI,
                            "content": output_content.strip(),
                            "summary": None,
                        }
                    )

                # Update progress every 10 rows
                if row_count % 10 == 0:
                    progress = min(90, 5 + (row_count * 85 // max(row_count, 1)))
                    progress_recorder.set_progress(progress, 100, f"Processing row {row_count}...")

            if not evaluation_messages:
                message = "No valid messages found in CSV"
                _save_dataset_error(dataset, message)
                return {"success": False, "error": message}

            # Bulk create messages
            progress_recorder.set_progress(95, 100, "Creating messages...")
            created_messages, _ = dataset.add_messages(evaluation_messages)

            # Mark as completed
            dataset.status = DatasetCreationStatus.COMPLETED
            dataset.job_id = ""
            dataset.save(update_fields=["status", "job_id"])

            progress_recorder.set_progress(100, 100, "Import complete")

            return {
                "success": True,
                "created_count": len(created_messages),
                "total_rows": row_count,
            }
    except Exception as e:
        logger.exception(f"Unexpected error in CSV creation task for dataset {dataset_id}: {e}")
        message = "An unexpected error occurred while processing the CSV file"
        _save_dataset_error(dataset, message)
        return {"success": False, "error": message}
    finally:
        csv_file.delete()


def _parse_csv_content(csv_content, progress_recorder):
    """Parse CSV content and return rows and columns."""
    csv_reader = csv.DictReader(StringIO(csv_content))
    columns = csv_reader.fieldnames or []
    rows = list(csv_reader)
    progress_recorder.set_progress(5, 100, "Parsing CSV...")
    return rows, columns


def _extract_row_data(row):
    """Extract and validate data from a CSV row."""
    input_content = row.get("input_content", "").strip()
    output_content = row.get("output_content", "").strip()

    if not input_content or not output_content:
        raise ValueError("Missing input or output content")

    # Extract context from context.* columns
    context = {}
    for col_name, value in row.items():
        if col_name.startswith("context.") and value:
            context_key = col_name.removeprefix("context.")
            context[context_key] = parse_csv_value_as_json(value)

    # Extract participant_data from participant_data.* columns
    participant_data = {}
    for col_name, value in row.items():
        if col_name.startswith("participant_data.") and value:
            key = col_name.removeprefix("participant_data.")
            participant_data[key] = parse_csv_value_as_json(value)

    # Extract session_state from session_state.* columns
    session_state = {}
    for col_name, value in row.items():
        if col_name.startswith("session_state.") and value:
            key = col_name.removeprefix("session_state.")
            session_state[key] = parse_csv_value_as_json(value)

    # Parse history if present
    history = []
    history_text = row.get("history", "").strip()
    if history_text:
        try:
            history = parse_history_text(history_text)
        except HistoryParseException as exc:
            raise ValueError("The history column could not be parsed") from exc

    return {
        "input_content": input_content,
        "output_content": output_content,
        "context": context,
        "participant_data": participant_data,
        "session_state": session_state,
        "history": history,
    }


def _update_existing_message(dataset, message_id, row_data, team):
    """Update an existing message with new data."""
    message = EvaluationMessage.objects.get(id=message_id, evaluationdataset=dataset, evaluationdataset__team=team)

    old_input_content = message.input.get("content", "")
    old_output_content = message.output.get("content", "")
    old_history = message.history
    old_context = message.context
    old_participant_data = message.participant_data
    old_session_state = message.session_state

    new_input_content = row_data["input_content"]
    new_output_content = row_data["output_content"]
    new_history = row_data["history"]
    new_context = row_data.get("context", {})
    new_participant_data = row_data.get("participant_data", {})
    new_session_state = row_data.get("session_state", {})

    input_content_changed = old_input_content != new_input_content
    output_content_changed = old_output_content != new_output_content
    history_changed = old_history != new_history
    context_chagned = old_context != new_context
    participant_data_changed = old_participant_data != new_participant_data
    session_state_changed = old_session_state != new_session_state

    any_content_changed = (
        input_content_changed
        or output_content_changed
        or history_changed
        or context_chagned
        or participant_data_changed
        or session_state_changed
    )

    message.context = new_context
    message.participant_data = new_participant_data
    message.session_state = new_session_state

    if history_changed:
        message.history = new_history

    if input_content_changed:
        message.input = EvaluationMessageContent(content=new_input_content, role="human").model_dump()
        message.input_chat_message = None

    if output_content_changed:
        message.output = EvaluationMessageContent(content=new_output_content, role="ai").model_dump()
        message.expected_output_chat_message = None

    if any_content_changed:
        message.session = None
        if message.metadata:
            message.metadata.pop("session_id", None)
            message.metadata.pop("experiment_id", None)
            message.metadata.update({"last_modified": "csv_upload"})

    message.save()

    return any_content_changed


def _create_new_message(dataset, row_data):
    """Create a new message and add it to the dataset."""
    message = EvaluationMessage.objects.create(
        input=EvaluationMessageContent(content=row_data["input_content"], role="human").model_dump(),
        output=EvaluationMessageContent(content=row_data["output_content"], role="ai").model_dump(),
        context=row_data["context"],
        participant_data=row_data["participant_data"],
        session_state=row_data["session_state"],
        history=row_data["history"],
        metadata={"created_mode": "csv_upload"},
    )
    dataset.messages.add(message)


def process_csv_rows(dataset, rows, columns, progress_recorder, team):
    """Process all CSV rows and return statistics."""
    stats = {"updated_count": 0, "created_count": 0, "error_messages": []}
    total_rows = len(rows)
    has_id_column = "id" in columns

    for row_index, row in enumerate(rows):
        try:
            row_data = _extract_row_data(row)
            updating_existing_message = has_id_column and row.get("id")
            if updating_existing_message:
                try:
                    message_id = int(row["id"])
                except ValueError:
                    stats["error_messages"].append(f"Row {row_index + 1}: Invalid ID format")
                    continue

                try:
                    if _update_existing_message(dataset, message_id, row_data, team):
                        stats["updated_count"] += 1
                except EvaluationMessage.DoesNotExist:
                    stats["error_messages"].append(f"Row {row_index + 1}: Message with ID {message_id} not found")
                    continue
            else:
                _create_new_message(dataset, row_data)
                stats["created_count"] += 1

        except Exception as e:
            stats["error_messages"].append(f"Row {row_index + 1}: {str(e)}")
            continue

        processed_rows = row_index + 1
        progress = int(10 + (processed_rows / total_rows) * 85)  # 10-95% for processing
        progress_recorder.set_progress(progress, 100, f"Processing row {processed_rows}/{total_rows}")

    return stats


@shared_task(bind=True, base=TaskbadgerTask)
def upload_evaluation_run_results_task(self, evaluation_run_id, csv_data, team_id, column_mappings=None):
    """
    Process CSV upload for evaluation run results asynchronously with progress tracking.
    csv_data: List of dictionaries representing CSV rows
    column_mappings: Dictionary mapping column names to evaluator names
    """
    progress_recorder = ProgressRecorder(self)
    return _upload_evaluation_run_results(progress_recorder, evaluation_run_id, csv_data, team_id, column_mappings)


def _upload_evaluation_run_results(progress_recorder, evaluation_run_id, csv_data, team_id, column_mappings=None):
    if not csv_data:
        return {"success": False, "error": "CSV file is empty"}

    try:
        evaluation_run = EvaluationRun.objects.select_related("team").get(id=evaluation_run_id, team_id=team_id)
        team = evaluation_run.team
        with current_team(team):
            stats = process_evaluation_results_csv_rows(
                evaluation_run, csv_data, column_mappings or {}, progress_recorder, team
            )
            # Re-compute aggregates after updating results
            compute_aggregates_for_run(evaluation_run)
            progress_recorder.set_progress(100, 100, "Upload complete")
            return {
                "success": True,
                "updated_count": stats["updated_count"],
                "created_count": stats["created_count"],
                "total_processed": stats["updated_count"] + stats["created_count"],
                "errors": stats["error_messages"],
            }

    except Exception as e:
        logger.error(f"Error in CSV upload task for evaluation run {evaluation_run_id}: {str(e)}")
        return {"success": False, "error": str(e)}


def process_evaluation_results_csv_rows(evaluation_run, csv_data, column_mappings, progress_recorder, team):
    """Process all CSV rows for evaluation results and return statistics."""
    stats = {"updated_count": 0, "created_count": 0, "error_messages": []}
    total_rows = len(csv_data)

    columns = list(csv_data[0].keys()) if csv_data else []
    has_id_column = "id" in columns

    evaluator_ids = evaluation_run.config.evaluators.all().values_list("id", flat=True)

    all_evaluation_results = EvaluationResult.objects.filter(run=evaluation_run, team=team)
    results_lookup = defaultdict(dict)
    for result in all_evaluation_results:
        results_lookup[result.message_id].update({result.evaluator_id: result})

    for row_index, row in enumerate(csv_data):
        try:
            if not has_id_column or not row.get("id"):
                stats["error_messages"].append(
                    f"Row {row_index + 1}: Missing 'id' column - cannot update results without ID"
                )
                continue

            try:
                message_id = int(row["id"])
            except ValueError:
                stats["error_messages"].append(f"Row {row_index + 1}: Invalid ID format")
                continue

            if message_id not in results_lookup:
                stats["error_messages"].append(
                    f"Row {row_index + 1}: No evaluation results found for message ID {message_id}"
                )
                continue

            for column_name, evaluator_id in column_mappings.items():
                if column_name not in row:
                    continue

                value = row[column_name]
                if value is None:
                    value = ""

                result_key = column_name
                if "(" in column_name and column_name.endswith(")"):
                    result_key = column_name[: column_name.rfind("(")].strip()

                try:
                    evaluator_id = int(evaluator_id)
                    if evaluator_id not in evaluator_ids:
                        stats["error_messages"].append(
                            f"Row {row_index + 1}: Evaluator with ID '{evaluator_id}' not found"
                        )
                        continue
                except (ValueError, TypeError):
                    stats["error_messages"].append(f"Row {row_index + 1}: Invalid evaluator ID '{evaluator_id}'")
                    continue

                evaluation_result = results_lookup.get(message_id, {}).get(evaluator_id)
                if evaluation_result:
                    updated_output = evaluation_result.output.copy()

                    if "result" not in updated_output:
                        updated_output["result"] = {}

                    current_value = updated_output["result"].get(result_key)

                    if current_value is not None and value:
                        # attempt to preserve types
                        if isinstance(current_value, int):
                            with contextlib.suppress(ValueError):
                                value = int(value)
                        elif isinstance(current_value, float):
                            with contextlib.suppress(ValueError):
                                value = float(value)
                    elif value:
                        # optimistically try to convert new values
                        try:
                            value = int(value)
                        except ValueError:
                            with contextlib.suppress(ValueError):
                                value = float(value)

                    if current_value != value:
                        updated_output["result"][result_key] = value
                        evaluation_result.output = updated_output
                        evaluation_result.save()
                        stats["updated_count"] += 1
                else:
                    stats["error_messages"].append(f"Row {row_index + 1}: No results foundand message {message_id}")
        except Exception as e:
            stats["error_messages"].append(f"Row {row_index + 1}: {str(e)}")
            continue

        processed_rows = row_index + 1
        progress = int(10 + (processed_rows / total_rows) * 85)  # 10-95% for processing
        progress_recorder.set_progress(progress, 100, f"Processing row {processed_rows}/{total_rows}")

    return stats


@shared_task(bind=True, base=TaskbadgerTask)
def create_dataset_from_session_messages_task(
    self, dataset_id, team_id, session_ids, filtered_session_ids, filter_query, timezone
):
    """
    Clone messages from sessions asynchronously.

    Args:
        dataset_id: ID of the EvaluationDataset to populate
        team_id: ID of the team
        session_ids: List of session external IDs to clone from
        filtered_session_ids: List of filtered session external IDs
        filter_query: Serialized filter parameters as query string (or None)
        timezone: Timezone for filtering
    """

    progress_recorder = ProgressRecorder(self)
    dataset = None

    try:
        dataset = EvaluationDataset.objects.select_related("team").get(id=dataset_id, team_id=team_id)
    except EvaluationDataset.DoesNotExist:
        logger.error(f"Dataset {dataset_id} not found for team {team_id}")
        return {"success": False, "error": "Dataset not found"}

    team = dataset.team
    dataset.status = DatasetCreationStatus.PROCESSING
    dataset.save(update_fields=["status"])

    try:
        progress_recorder.set_progress(0, 100, "Starting clone...")

        filter_params = FilterParams(QueryDict(filter_query)) if filter_query is not None else None

        with current_team(team):
            evaluation_messages = EvaluationMessage.create_from_sessions(
                team=team,
                external_session_ids=session_ids,
                filtered_session_ids=filtered_session_ids,
                filter_params=filter_params,
                timezone=timezone,
            )

            progress_recorder.set_progress(
                40, 100, f"Found {len(evaluation_messages)} messages, checking for duplicates..."
            )

            created_messages, duplicates_skipped = dataset.add_messages(evaluation_messages)

            dataset.status = DatasetCreationStatus.COMPLETED
            dataset.job_id = ""
            dataset.save(update_fields=["status", "job_id"])

            progress_recorder.set_progress(100, 100, "Clone complete")

            return {
                "success": True,
                "created_count": len(created_messages),
                "duplicates_skipped": duplicates_skipped,
            }

    except Exception as e:
        logger.exception(f"Error in clone task for dataset {dataset_id}: {e}")
        message = "An error occurred while cloning messages from sessions"
        _save_dataset_error(dataset, message)
        return {"success": False, "error": message}


@shared_task(bind=True, base=TaskbadgerTask)
def create_dataset_from_sessions_task(self, dataset_id, team_id, session_ids):
    """
    Create session-mode evaluation messages from sessions asynchronously.

    Each session becomes one EvaluationMessage with the full conversation as history.

    Args:
        dataset_id: ID of the EvaluationDataset to populate
        team_id: ID of the team
        session_ids: List of session external IDs
    """
    progress_recorder = ProgressRecorder(self)
    dataset = None

    try:
        dataset = EvaluationDataset.objects.select_related("team").get(id=dataset_id, team_id=team_id)
    except EvaluationDataset.DoesNotExist:
        logger.error(f"Dataset {dataset_id} not found for team {team_id}")
        return {"success": False, "error": "Dataset not found"}

    dataset.status = DatasetCreationStatus.PROCESSING
    dataset.save(update_fields=["status"])

    try:
        progress_recorder.set_progress(0, 100, "Starting session-mode clone...")

        with current_team(dataset.team):
            evaluation_messages = make_session_evaluation_messages(session_ids, team=dataset.team)

            progress_recorder.set_progress(
                40, 100, f"Found {len(evaluation_messages)} sessions, checking for duplicates..."
            )

            created_messages, duplicates_skipped = dataset.add_messages(evaluation_messages)

            dataset.status = DatasetCreationStatus.COMPLETED
            dataset.job_id = ""
            dataset.save(update_fields=["status", "job_id"])

            progress_recorder.set_progress(100, 100, "Clone complete")

            return {
                "success": True,
                "created_count": len(created_messages),
                "duplicates_skipped": duplicates_skipped,
            }

    except Exception as e:
        logger.exception(f"Error in session-mode clone task for dataset {dataset_id}: {e}")
        message = "An error occurred while creating session-mode messages"
        _save_dataset_error(dataset, message)
        return {"success": False, "error": message}


def _get_bulk_results_queryset(config, team):
    """Return the most recent EvaluationResult per (message, evaluator) across all
    completed FULL/DELTA runs for *config*, pushing deduplication into the DB via
    DISTINCT ON so only the latest-run row per pair is fetched."""
    return (
        EvaluationResult.objects.filter(
            run__config=config,
            run__status=EvaluationRunStatus.COMPLETED,
            run__type__in=[EvaluationRunType.FULL, EvaluationRunType.DELTA],
            team=team,
        )
        .select_related("message__session__experiment", "evaluator", "session", "run")
        .prefetch_related("applied_tags__tag")
        .order_by("message_id", "evaluator_id", "-run__created_at")
        .distinct("message_id", "evaluator_id")
    )


@shared_task(base=TaskbadgerTask)
def export_evaluation_bulk_results_task(evaluation_config_id, team_id):
    """
    Async export of the most recent evaluation result for each dataset item,
    across all completed evaluation runs for the given config.

    Returns {"file_id": <id>} on success.
    """
    try:
        config = EvaluationConfig.objects.select_related("team").get(id=evaluation_config_id, team_id=team_id)
        team = config.team

        with current_team(team):
            results = _get_bulk_results_queryset(config, team)
            table_data = build_evaluation_table_data(results)

            csv_buffer = StringIO()
            write_evaluation_csv(csv.writer(csv_buffer), table_data)

            filename = f"{config.name}_latest_results_{timezone.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv"
            file_obj = File.objects.create(
                name=filename,
                team=team,
                content_type="text/csv",
                file=ContentFile(csv_buffer.getvalue().encode("utf-8"), name=filename),
                purpose=FilePurpose.DATA_EXPORT,
                expiry_date=timezone.now() + timedelta(days=7),
            )

            return {"file_id": file_obj.id}

    except Exception as e:
        logger.exception(f"Error exporting bulk evaluation results for config {evaluation_config_id}: {e}")
        return {"error": str(e)}
