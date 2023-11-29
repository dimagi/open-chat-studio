import json
import sys
from contextlib import contextmanager

from celery import shared_task
from celery_progress.backend import ProgressRecorder
from django.utils import timezone

from apps.analysis.log import Logger
from apps.analysis.models import AnalysisRun, RunStatus
from apps.analysis.pipelines import get_source_pipeline
from apps.analysis.steps import PipelineContext, StepContext


@contextmanager
def run_context(run):
    run.start_time = timezone.now()
    run.status = RunStatus.RUNNING
    run.save()

    try:
        yield
        run.status = RunStatus.SUCCESS
    except Exception as e:
        run.status = RunStatus.ERROR
        run.error = str(e)
    finally:
        run.end_time = timezone.now()
        run.save()


@shared_task
def run_pipeline(run_id: int):
    run = AnalysisRun.objects.get(id=run_id)
    with run_context(run):
        pipline_context = PipelineContext(logger=Logger(sys.stdout), params=run.params)
        pipeline = get_source_pipeline(run.analysis.source)
        result = pipeline.run(pipline_context, StepContext.initial())
        run.log = pipline_context.log.to_json()
        run.output = json.loads(result.data.to_json(orient="records"))
