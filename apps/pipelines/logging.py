import uuid

from django.db import transaction
from langchain_core.callbacks import BaseCallbackHandler
from loguru import logger


class PipelineLoggingCallbackHandler(BaseCallbackHandler):
    """Handles logging within the pipeline

    Since Langchain / Langgraph runs in separate threads, we append to a list
    in a thread-safe manner, then periodically write to the DB later.

    """

    def __init__(self, pipeline, verbose=False) -> None:
        from apps.pipelines.models import PipelineRun

        self.pipeline = pipeline
        self.pipeline_run = PipelineRun.objects.create(pipeline=self.pipeline, status="SUCCESS")
        self.logger = get_logger(uuid.uuid4().hex, self.pipeline_run)
        self.log = ""
        self.depth = 0
        self.errored = False

    def on_chain_start(self, serialized, inputs, *args, **kwargs):
        self.depth += 1
        self.logger.info(f"{kwargs.get('name', serialized.get('name'))} starting")
        super().on_chain_start(serialized, inputs, *args, **kwargs)

    def on_chain_end(self, outputs, **kwargs):
        self.depth -= 1
        self.logger.info(f"chain ending {outputs}")
        if self.depth == 0:
            self.logger.info(outputs)

    def on_chain_error(self, error, *args, **kwargs):
        self.logger.error(error)


class LogHandler:
    def __init__(self, pipeline_run_id):
        self.pipeline_run_id = pipeline_run_id
        self.logs = {"entries": []}

    def write_log(self, entries):
        # Write the logs in a separate transaction in a thread safe way
        from apps.pipelines.models import PipelineRun

        with transaction.atomic():
            PipelineRun.objects.filter(pk=self.pipeline_run_id).update(log=entries)
            # This just swallows the errors - if filter doesn't match anything, this will just do nothing

    def __call__(self, message):
        from apps.pipelines.models import LogEntry

        record = message.record
        log_entry = LogEntry(
            **{
                "time": record["time"].strftime("%Y-%m-%d %H:%M:%S.%f"),
                "level": record["level"].name,
                "message": record["message"],
            }
        )
        self.logs["entries"].append(log_entry.model_dump())
        self.write_log(self.logs)


def get_logger(name, pipeline_run):
    log = logger.bind(name=name)
    log.level("DEBUG")
    log.remove()
    log.add(LogHandler(pipeline_run.pk))
    return log
