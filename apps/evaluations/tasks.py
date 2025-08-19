import csv
import logging
from collections.abc import Iterable
from datetime import timedelta
from io import StringIO
from typing import cast

from celery import chord, shared_task
from celery_progress.backend import ProgressRecorder
from django.utils import timezone

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.tasks import handle_evaluation_message
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.evaluations.const import PREVIEW_SAMPLE_SIZE
from apps.evaluations.models import (
    EvaluationDataset,
    EvaluationMessage,
    EvaluationMessageContent,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationRunType,
    Evaluator,
)
from apps.evaluations.utils import parse_history_text
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.teams.utils import current_team

logger = logging.getLogger("ocs.evaluations")


@shared_task
def evaluate_single_message_task(evaluation_run_id, evaluator_ids, message_id):
    """
    Run all evaluations over a single message.
    First runs the message through the bot, then runs the evaluator.
    ExperimentSessions created in this task are deleted periodically by cleanup_old_evaluation_data
    """
    evaluation_run = EvaluationRun.objects.select_related("team").get(id=evaluation_run_id)

    with current_team(evaluation_run.team):
        message = EvaluationMessage.objects.get(id=message_id)
        # Only run bot generation if an experiment version is configured
        generation_experiment = evaluation_run.generation_experiment
        session_id, bot_response = None, ""
        if generation_experiment is not None:
            session_id, bot_response = run_bot_generation(evaluation_run.team, message, generation_experiment)

        for evaluator_id in evaluator_ids:
            evaluator = Evaluator.objects.get(id=evaluator_id)
            try:
                result = evaluator.run(message, bot_response or "")
                EvaluationResult.objects.create(
                    message=message,
                    run=evaluation_run,
                    evaluator=evaluator,
                    output=result.model_dump(),
                    team=evaluation_run.team,
                    session_id=session_id,
                )
            except Exception as e:
                logger.exception(f"Error running evaluator {evaluator.id} on message {message.id}: {e}")
                EvaluationResult.objects.create(
                    message=message,
                    run=evaluation_run,
                    evaluator=evaluator,
                    output={"error": str(e)},
                    team=evaluation_run.team,
                    session_id=session_id,
                )


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
        )

        # Populate history on the chat with the history from the EvaluationMessage
        if message.history:
            history_messages = [
                ChatMessage(
                    chat=chat,
                    message_type=history_entry.get("message_type", ChatMessageType.HUMAN),
                    content=history_entry.get("content", ""),
                    summary=history_entry.get("summary"),
                )
                for history_entry in message.history
            ]
            ChatMessage.objects.bulk_create(history_messages)

        # TODO: Populate participant data?
    except Exception as e:
        logger.exception(f"Error populating eval data {message.id}: {e}")
        # Don't fail the entire evaluation if bot generation fails
        return None, None

    try:
        # Extract the input message content
        input_content = message.input.get("content", "")

        # Call the bot with the evaluation message and session
        bot_response = handle_evaluation_message(
            experiment_version=experiment,
            experiment_channel=evaluation_channel,
            message_text=input_content,
            session=session,
        )
        response_content = bot_response.content
        logger.debug(f"Bot generated response for evaluation message {message.id}: {response_content}")

        return session.id, response_content

    except Exception as e:
        logger.exception(f"Error generating bot response for evaluation message {message.id}: {e}")
        # Don't fail the entire evaluation if bot generation fails
        return session.id, None


@shared_task
def mark_evaluation_complete(results, evaluation_run_id):
    """
    Callback task that marks an evaluation run as complete.
    This is called when all tasks in a chord have finished.

    Args:
        results: List of results from the group tasks (unused but required by chord)
        evaluation_run_id: ID of the evaluation run to mark complete
    """
    try:
        evaluation_run = EvaluationRun.objects.get(id=evaluation_run_id)
        if evaluation_run.status == EvaluationRunStatus.PROCESSING:
            evaluation_run.mark_complete()
    except Exception as e:
        logger.exception(f"Error marking evaluation run {evaluation_run_id} complete: {e}")


@shared_task(bind=True)
def run_evaluation_task(self, evaluation_run_id):
    """
    Spawns an evaluator task for each message
    """
    try:
        evaluation_run = (
            EvaluationRun.objects.select_related("config", "team")
            .prefetch_related("config__evaluators", "config__dataset__messages")
            .get(id=evaluation_run_id)
        )

        evaluation_run.status = EvaluationRunStatus.PROCESSING
        evaluation_run.save(update_fields=["status"])

        with current_team(evaluation_run.team):
            config = evaluation_run.config
            evaluators = list(cast(Iterable[Evaluator], config.evaluators.all()))
            message_queryset = config.dataset.messages.all()
            if evaluation_run.type == EvaluationRunType.PREVIEW:
                messages = list(message_queryset[:PREVIEW_SAMPLE_SIZE])
            else:
                messages = list(message_queryset)

            if len(evaluators) == 0 or len(messages) == 0:
                evaluation_run.job_id = ""
                evaluation_run.mark_complete(save=False)
                evaluation_run.save(update_fields=["finished_at", "status", "job_id"])
                return

            # Create chord with group and callback
            chord_result = chord(
                evaluate_single_message_task.chunks(
                    [(evaluation_run_id, [e.id for e in evaluators], message.id) for message in messages], 5
                ).group()
            )(mark_evaluation_complete.s(evaluation_run_id))

            chord_result.parent.save()
            job = chord_result.parent

            evaluation_run.job_id = job.id
            evaluation_run.save(update_fields=["job_id"])
    except Exception as e:
        logger.exception(f"Error starting evaluation run {evaluation_run_id}: {e}")
        evaluation_run = EvaluationRun.objects.get(id=evaluation_run_id)
        evaluation_run.status = EvaluationRunStatus.FAILED
        evaluation_run.error_message = str(e)
        evaluation_run.job_id = ""
        evaluation_run.save(update_fields=["status", "error_message", "job_id"])


@shared_task
def cleanup_old_evaluation_data():
    """Delete ExperimentSessions that were created during evaluation runs and
    are older than one week.

    """
    one_week_ago = timezone.now() - timedelta(days=7)
    old_evaluation_sessions = ExperimentSession.objects.filter(
        experiment_channel__platform=ChannelPlatform.EVALUATIONS, created_at__lt=one_week_ago
    )

    sessions_count = old_evaluation_sessions.count()
    if sessions_count == 0:
        logger.info("No old evaluation sessions found to cleanup")
        return
    deleted_sessions = old_evaluation_sessions.delete()

    logger.info(f"Cleanup completed: deleted {deleted_sessions[0]} evaluation sessions")


@shared_task
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


@shared_task(bind=True)
def upload_dataset_csv_task(self, dataset_id, csv_content, team_id):
    """
    Process CSV upload for dataset asynchronously with progress tracking.
    """
    progress_recorder = ProgressRecorder(self)

    try:
        dataset = EvaluationDataset.objects.select_related("team").get(id=dataset_id, team_id=team_id)
        team = dataset.team

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

    except Exception as e:
        logger.error(f"Error in CSV upload task for dataset {dataset_id}: {str(e)}")
        return {"success": False, "error": str(e)}


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
            context_key = col_name[8:]  # Remove "context." prefix
            context[context_key] = value

    # Parse history if present
    history = []
    history_text = row.get("history", "").strip()
    if history_text:
        history = parse_history_text(history_text)

    return {
        "input_content": input_content,
        "output_content": output_content,
        "context": context,
        "history": history,
    }


def _update_existing_message(dataset, message_id, row_data, team):
    """Update an existing message with new data."""
    message = EvaluationMessage.objects.get(id=message_id, evaluationdataset=dataset, evaluationdataset__team=team)

    old_input_content = message.input.get("content", "")
    old_output_content = message.output.get("content", "")
    old_history = message.history
    old_context = message.context

    new_input_content = row_data["input_content"]
    new_output_content = row_data["output_content"]
    new_history = row_data["history"]
    new_context = row_data["context"]

    input_content_changed = old_input_content != new_input_content
    output_content_changed = old_output_content != new_output_content
    history_changed = old_history != new_history
    context_chagned = old_context != new_context

    any_content_changed = input_content_changed or output_content_changed or history_changed or context_chagned

    message.context = row_data["context"]

    if history_changed:
        message.history = new_history

    if input_content_changed:
        message.input = EvaluationMessageContent(content=new_input_content, role="human").model_dump()
        message.input_chat_message = None

    if output_content_changed:
        message.output = EvaluationMessageContent(content=new_output_content, role="ai").model_dump()
        message.expected_output_chat_message = None

    if any_content_changed and message.metadata:
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
                    stats["error_messages"].append(
                        f"Row {row_index + 1}: Message with ID {message_id} not found"
                    )
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
