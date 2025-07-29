import logging
from collections.abc import Iterable
from datetime import timedelta
from typing import cast

from celery import chord, shared_task
from django.utils import timezone

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.tasks import handle_evaluation_message
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.evaluations.models import EvaluationMessage, EvaluationResult, EvaluationRun, EvaluationRunStatus, Evaluator
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
        logger.info(f"Bot generated response for evaluation message {message.id}: {response_content}")

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
            messages = list(config.dataset.messages.all())

            if len(evaluators) == 0 or len(messages) == 0:
                evaluation_run.job_id = ""
                evaluation_run.mark_complete(save=False)
                evaluation_run.save(update_fields=["finished_at", "status", "job_id"])
                return

            # Create chord with group and callback
            chord_result = chord(
                evaluate_single_message_task.chunks(
                    [(evaluation_run_id, [e.id for e in evaluators], message.id) for message in messages], 5
                )
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
