import logging
from collections.abc import Iterable
from typing import cast

from celery import shared_task
from celery_progress.backend import ProgressRecorder
from django.utils import timezone

from apps.teams.utils import current_team

from .models import EvaluationResult, EvaluationRun, EvaluationRunStatus, Evaluator

logger = logging.getLogger("ocs.evaluations")


@shared_task(bind=True)
def run_evaluation_task(self, evaluation_run_id):
    progress_recorder = ProgressRecorder(self)

    try:
        evaluation_run = EvaluationRun.objects.select_related("config", "team").get(id=evaluation_run_id)

        evaluation_run.status = EvaluationRunStatus.PROCESSING
        evaluation_run.save(update_fields=["status"])

        progress_recorder.set_progress(0, 100, description="Starting evaluation...")

        with current_team(evaluation_run.team):
            config = evaluation_run.config
            evaluators = list(cast(Iterable[Evaluator], config.evaluators.all()))
            messages = list(config.dataset.messages.all())

            total_tasks = len(evaluators) * len(messages)
            current_task = 0

            progress_recorder.set_progress(0, 100, description=f"Processing {total_tasks} evaluations...")

            results = []
            for evaluator in evaluators:
                for message in messages:
                    current_task += 1
                    progress_value = int((current_task / total_tasks) * 100)
                    progress_recorder.set_progress(
                        progress_value,
                        100,
                        description=f"Running {evaluator.name} on message {current_task}/{total_tasks}",
                    )

                    try:
                        result = evaluator.run(message)
                        results.append(
                            EvaluationResult.objects.create(
                                message=message,
                                run=evaluation_run,
                                evaluator=evaluator,
                                output=result.model_dump(),
                                team=evaluation_run.team,
                            )
                        )
                    except Exception as e:
                        logger.exception(f"Error running evaluator {evaluator.id} on message {message.id}: {e}")
                        # Continue with other evaluations even if one fails
                        results.append(
                            EvaluationResult.objects.create(
                                message=message,
                                run=evaluation_run,
                                evaluator=evaluator,
                                output={"error": str(e)},
                                team=evaluation_run.team,
                            )
                        )

            evaluation_run.finished_at = timezone.now()
            evaluation_run.status = EvaluationRunStatus.COMPLETED
            evaluation_run.job_id = ""
            evaluation_run.save(update_fields=["finished_at", "status", "job_id"])

            progress_recorder.set_progress(100, 100, description="Evaluation complete")

    except Exception as e:
        logger.exception(f"Error processing evaluation run {evaluation_run_id}: {e}")
        evaluation_run = EvaluationRun.objects.get(id=evaluation_run_id)
        evaluation_run.status = EvaluationRunStatus.FAILED
        evaluation_run.error_message = str(e)
        evaluation_run.job_id = ""
        evaluation_run.save(update_fields=["status", "error_message", "job_id"])
        progress_recorder.set_progress(100, 100, description=f"Evaluation failed: {str(e)}")
