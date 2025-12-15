import csv
import logging
import math
from collections import defaultdict
from collections.abc import Iterable
from datetime import timedelta
from io import StringIO
from typing import cast

from celery import chord, shared_task
from celery_progress.backend import ProgressRecorder
from django.utils import timezone
from taskbadger.celery import Task as TaskbadgerTask

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.tasks import handle_evaluation_message
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.evaluations.const import PREVIEW_SAMPLE_SIZE
from apps.evaluations.exceptions import HistoryParseException
from apps.evaluations.models import (
    DatasetCreationStatus,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationMessageContent,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationRunType,
    Evaluator,
)
from apps.evaluations.utils import parse_csv_value_as_json, parse_history_text
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.files.models import File
from apps.teams.utils import current_team

EVAL_SESSIONS_TTL_DAYS = 7

logger = logging.getLogger("ocs.evaluations")


def _save_dataset_error(dataset: EvaluationDataset, error_message: str):
    """Helper to save dataset error status and clear job_id."""
    dataset.status = DatasetCreationStatus.FAILED
    dataset.error_message = error_message
    dataset.job_id = ""
    dataset.save(update_fields=["status", "error_message", "job_id"])


@shared_task(base=TaskbadgerTask, rate_limit="0.5/s")
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
            state=message.session_state,
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

        # Call the bot with the evaluation message and session
        bot_response = handle_evaluation_message(
            experiment_version=experiment,
            experiment_channel=evaluation_channel,
            message_text=input_content,
            session=session,
            participant_data=message.participant_data,
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


@shared_task(base=TaskbadgerTask)
def mark_evaluation_complete(results, evaluation_run_id):
    """
    Callback task that marks an evaluation run as complete.
    This is called when all tasks in a chord have finished.

    Args:
        results: List of results from the group tasks (unused but required by chord)
        evaluation_run_id: ID of the evaluation run to mark complete
    """
    from apps.evaluations.aggregation import compute_aggregates_for_run

    try:
        evaluation_run = EvaluationRun.objects.get(id=evaluation_run_id)
        if evaluation_run.status == EvaluationRunStatus.PROCESSING:
            evaluation_run.mark_complete()
            compute_aggregates_for_run(evaluation_run)
    except Exception as e:
        logger.exception(f"Error marking evaluation run {evaluation_run_id} complete: {e}")


@shared_task(base=TaskbadgerTask)
def run_evaluation_task(evaluation_run_id):
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
            concurrency_limit = 10
            chunk_size = math.ceil(len(messages) / concurrency_limit)
            evaluator_ids = [e.id for e in evaluators]
            chord_result = chord(
                evaluate_single_message_task.chunks(
                    [(evaluation_run_id, evaluator_ids, message.id) for message in messages], chunk_size
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
    deleted_sessions = old_evaluation_sessions.delete()

    logger.info(f"Cleanup completed: deleted {deleted_sessions[0]} evaluation sessions")


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
            created_messages = EvaluationMessage.objects.bulk_create(evaluation_messages)
            dataset.messages.add(*created_messages)

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

    if not csv_data:
        return {"success": False, "error": "CSV file is empty"}

    progress_recorder = ProgressRecorder(self)

    try:
        evaluation_run = EvaluationRun.objects.select_related("team").get(id=evaluation_run_id, team_id=team_id)
        team = evaluation_run.team
        with current_team(team):
            stats = process_evaluation_results_csv_rows(
                evaluation_run, csv_data, column_mappings or {}, progress_recorder, team
            )
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
def create_dataset_from_sessions_task(
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
    from django.http import QueryDict

    from apps.web.dynamic_filters.datastructures import FilterParams

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

            # Get existing chat message pairs to avoid duplicates
            existing_chat_message_pairs = set(
                dataset.messages.filter(
                    input_chat_message_id__isnull=False,
                    expected_output_chat_message_id__isnull=False,
                ).values_list("input_chat_message_id", "expected_output_chat_message_id")
            )

            # Filter out duplicates based on ChatMessage IDs
            messages_to_add = []
            for msg in evaluation_messages:
                chat_pair = (msg.input_chat_message_id, msg.expected_output_chat_message_id)
                if chat_pair not in existing_chat_message_pairs:
                    messages_to_add.append(msg)
                    existing_chat_message_pairs.add(chat_pair)

            if not messages_to_add:
                dataset.status = DatasetCreationStatus.COMPLETED
                dataset.job_id = ""
                dataset.save(update_fields=["status", "job_id"])
                progress_recorder.set_progress(100, 100, "Clone complete - no new messages to add")
                return {"success": True, "created_count": 0, "duplicates_skipped": len(evaluation_messages)}

            progress_recorder.set_progress(70, 100, f"Creating {len(messages_to_add)} new messages...")

            created_messages = EvaluationMessage.objects.bulk_create(messages_to_add)
            dataset.messages.add(*created_messages)

            dataset.status = DatasetCreationStatus.COMPLETED
            dataset.job_id = ""
            dataset.save(update_fields=["status", "job_id"])

            progress_recorder.set_progress(100, 100, "Clone complete")

            duplicates_skipped = len(evaluation_messages) - len(messages_to_add)
            return {"success": True, "created_count": len(created_messages), "duplicates_skipped": duplicates_skipped}

    except Exception as e:
        logger.exception(f"Error in clone task for dataset {dataset_id}: {e}")
        message = "An error occurred while cloning messages from sessions"
        _save_dataset_error(dataset, message)
        return {"success": False, "error": message}
