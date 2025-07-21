import logging
from collections.abc import Iterable
from typing import cast

from celery import chord, shared_task

from apps.channels.models import ChannelPlatform
from apps.evaluations.models import EvaluationMessage, EvaluationResult, EvaluationRun, EvaluationRunStatus, Evaluator
from apps.teams.utils import current_team

logger = logging.getLogger("ocs.evaluations")


@shared_task(bind=True)
def run_single_evaluation_task(self, evaluation_run_id, evaluator_id, message_id):
    """
    Run a single evaluation for one evaluator on one message.
    First runs the message through the bot, then runs the evaluator.
    """
    evaluation_run = EvaluationRun.objects.select_related("team").get(id=evaluation_run_id)

    with current_team(evaluation_run.team):
        evaluator = Evaluator.objects.get(id=evaluator_id)
        message = EvaluationMessage.objects.get(id=message_id)

        try:
            # Call the bot first to generate a response
            _run_bot_generation(evaluation_run.team, message)

            # Then run the evaluator
            result = evaluator.run(message)
            EvaluationResult.objects.create(
                message=message,
                run=evaluation_run,
                evaluator=evaluator,
                output=result.model_dump(),
                team=evaluation_run.team,
            )
        except Exception as e:
            logger.exception(f"Error running evaluator {evaluator.id} on message {message.id}: {e}")
            EvaluationResult.objects.create(
                message=message,
                run=evaluation_run,
                evaluator=evaluator,
                output={"error": str(e)},
                team=evaluation_run.team,
            )


def _run_bot_generation(team, message: EvaluationMessage):
    """
    Run the evaluation message through the bot to generate a response.
    """
    from apps.channels.models import ExperimentChannel
    from apps.channels.tasks import handle_evaluation_message
    from apps.chat.models import Chat
    from apps.experiments.models import Experiment, ExperimentSession, Participant

    try:
        # Get the hardcoded experiment version
        # TODO: update this.
        experiment = Experiment.objects.get(public_id="abcbaf2c-c5a5-4ba6-802a-83a1e825d762")

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

        # TODO: Populate history on the session with the history from the EvaluationMessage?
        #  -- Do this by populating the chat with the history from the message by creating new ChatMessage objects
        # TODO: Populate participant data?

        # Extract the input message content
        input_content = message.input.get("content", "")

        # Call the bot with the evaluation message and session
        bot_response = handle_evaluation_message(
            experiment_version=experiment,
            experiment_channel=evaluation_channel,
            message_text=input_content,
            session=session,
        )

        logger.info(f"Bot generated response for evaluation message {message.id}: {bot_response.content}")

    except Exception as e:
        logger.exception(f"Error generating bot response for evaluation message {message.id}: {e}")
        # Don't fail the entire evaluation if bot generation fails
        pass


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
                run_single_evaluation_task.s(evaluation_run_id, evaluator.id, message.id)
                for message in messages
                for evaluator in evaluators
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
