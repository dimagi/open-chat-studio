import json
import logging
import sys
from contextlib import contextmanager

import pandas as pd
from celery import shared_task
from django.utils import timezone
from pydantic import BaseModel

from apps.analysis.log import Logger
from apps.analysis.models import AnalysisRun, RunStatus
from apps.analysis.pipelines import get_source_pipeline
from apps.analysis.steps import PipelineContext, StepContext

log = logging.getLogger(__name__)


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
        run.error = repr(e)
        log.exception("Error running analysis")
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
        run.output = output_to_json(result.data)


def compile_logs(pipeline):
    # TODO: get logs from all stepcontexts or something else
    # would be nice to be able to stream logs to the frontend - put run in pipeline context?
    pass


def output_to_json(output_data):
    if isinstance(output_data, (str, dict, list)):
        return output_data
    if isinstance(output_data, pd.DataFrame):
        return json.loads(output_data.to_json(orient="records"))
    if isinstance(output_data, BaseModel):
        return output_data.model_dump()
    return str(output_data)
