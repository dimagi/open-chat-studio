from langchain_core.callbacks import BaseCallbackHandler
from loguru import logger


class PipelineLoggingCallbackHandler(BaseCallbackHandler):
    def __init__(self, pipeline) -> None:
        from apps.pipelines.models import PipelineRun

        self.pipeline = pipeline
        self.pipeline_run = PipelineRun.objects.create(pipeline=self.pipeline, status="SUCCESS")
        self.logger = get_logger("foo", self.pipeline_run)
        self.log = ""
        self.depth = 0
        self.errored = False

    def on_chain_start(self, serialized, inputs, *args, **kwargs):
        self.depth += 1
        self.logger.info("chain starting")
        # apps.pipelines.models.PipelineRun.DoesNotExist: PipelineRun matching query does not exist.
        super().on_chain_start(serialized, inputs, *args, **kwargs)

    def on_chain_end(self, outputs, **kwargs):
        self.depth -= 1
        self.logger.info(outputs)
        # apps.pipelines.models.PipelineRun.DoesNotExist: PipelineRun matching query does not exist.
        if self.depth == 0:
            self.logger.info(outputs)

    def on_chain_error(self, error, *args, **kwargs):
        self.logger.error(error)


class LogHandler:
    def __init__(self, pipeline_run):
        self.pipeline_run = pipeline_run

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
        self.pipeline_run.refresh_from_db()
        entries = self.pipeline_run.log.get("entries", [])
        self.pipeline_run.log = {"entries": entries + [log_entry.model_dump()]}
        self.pipeline_run.save()


def get_logger(name, pipeline_run):
    log = logger.bind(name=name)
    log.level("DEBUG")
    log.remove()
    log.add(LogHandler(pipeline_run))
    return log
