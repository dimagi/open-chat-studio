import logging
from contextlib import contextmanager

from celery import chord
from celery import group as celery_group
from celery import shared_task
from django.utils import timezone

from apps.analysis.core import PipelineContext, StepContext
from apps.analysis.log import LogEntry, Logger, LogStream
from apps.analysis.models import AnalysisRun, RunGroup, RunStatus
from apps.analysis.pipelines import get_data_pipeline, get_source_pipeline
from apps.analysis.serializers import get_serializer_by_type

log = logging.getLogger(__name__)


class PipelineSplitSignal(Exception):
    """Exception used to signal that the pipeline has split"""

    pass


class RunStatusContext:
    def __init__(self, run: RunGroup | AnalysisRun, bubble_errors=True):
        self.run = run
        self.bubble_errors = bubble_errors

    def __enter__(self):
        self.run.start_time = timezone.now()
        self.run.status = RunStatus.RUNNING
        self.run.save()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            if exc_type == PipelineSplitSignal:
                return True
            log.exception("Error running analysis")
            self.run.status = RunStatus.ERROR
            self.run.error = repr(exc_val)
        else:
            self.run.status = RunStatus.SUCCESS
        self.run.end_time = timezone.now()
        self.run.save()
        return not self.bubble_errors


@contextmanager
def run_context(run, bubble_errors=True):
    """Context manager to create the pipeline context and manage run status."""
    log_stream = RunLogStream(run)
    params = run.group.params
    params["llm_model"] = run.group.analysis.llm_model
    pipeline_context = PipelineContext(run, log=Logger(log_stream), params=params, create_resources=True)

    with RunStatusContext(run, bubble_errors=bubble_errors):
        yield pipeline_context


class RunLogStream(LogStream):
    def __init__(self, run):
        self.run = run
        self.logs = []

    def write(self, entry: LogEntry):
        self.logs.append(entry.to_json())
        self.flush()

    def flush(self):
        self.run.log = {"entries": self.logs}
        self.run.save(update_fields=["log"])


@shared_task
def run_analysis(run_group_id: int):
    group = RunGroup.objects.select_related("team", "analysis", "analysis__llm_provider").get(id=run_group_id)

    with RunStatusContext(group):
        source_result = run_serial_pipeline(group, group.analysis.source, get_source_pipeline, StepContext.initial())

        if isinstance(source_result, list) and len(source_result) > 1:
            run_parallel_pipeline(group, source_result)
            raise PipelineSplitSignal()
        else:
            next_intput = source_result[0] if isinstance(source_result, list) else source_result
            run_serial_pipeline(group, group.analysis.pipeline, get_data_pipeline, next_intput)


def run_parallel_pipeline(group, contexts):
    tasks = []
    for context in contexts:
        assert context.resource, "Parallel pipeline requires resource to be created by source pipeline"
        run = AnalysisRun.objects.create(name=context.name, group=group, input_resource=context.resource)
        tasks.append(run_pipline_split.s(run.id))

    task_group = celery_group(tasks)
    callback_chord = chord(task_group, update_group_run_status.s(group_id=group.id))
    callback_chord.link_error(on_chord_error.s(group_id=group.id))
    callback_chord.apply_async()


@shared_task
def update_group_run_status(task_results, group_id: int):
    group = RunGroup.objects.get(id=group_id)
    group_status = (
        RunStatus.ERROR
        if any(status == RunStatus.ERROR for status in group.analysisrun_set.values_list("status", flat=True))
        else RunStatus.SUCCESS
    )
    group.status = group_status
    group.end_time = timezone.now()
    group.save()


@shared_task
def on_chord_error(request, exc, traceback, group_id: int):
    group = RunGroup.objects.get(id=group_id)
    group.status = RunStatus.ERROR
    group.error = repr(exc)
    group.end_time = timezone.now()
    group.save()


@shared_task(bind=True)
def run_pipline_split(self, run_id: int):
    run = AnalysisRun.objects.select_related(
        "group", "group__team", "group__analysis", "group__analysis__llm_provider"
    ).get(id=run_id)
    run.task_id = self.request.id
    run.save()

    resource = run.input_resource
    step_context = StepContext.initial(resource=resource, name=run.name)
    run_pipeline(run, run.group.analysis.pipeline, get_data_pipeline, step_context, bubble_errors=False)


def run_serial_pipeline(
    group: RunGroup, pipeline_id: str, pipeline_factory, context
) -> StepContext | list[StepContext]:
    run = AnalysisRun.objects.create(name=context.name, group=group)
    return run_pipeline(run, pipeline_id, pipeline_factory, context)


def run_pipeline(
    run: AnalysisRun, pipeline_id: str, pipeline_factory, context, bubble_errors=True
) -> StepContext | list[StepContext]:
    with run_context(run, bubble_errors=bubble_errors) as pipeline_context:
        pipeline = pipeline_factory(pipeline_id)
        result = pipeline.run(pipeline_context, context)
        process_pipeline_output(pipeline_context, result)
        return result


def process_pipeline_output(pipeline_context: PipelineContext, result: StepContext):
    run = pipeline_context.run
    if isinstance(result, list):
        run.output_summary = f"{len(result)} groups created"
        for res in result:
            run.output_summary += f"\n  - {res.name}"
    else:
        run.output_summary = get_serializer_by_type(result.data).get_summary(result.data)
