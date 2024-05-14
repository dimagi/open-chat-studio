from langchain_core.callbacks import BaseCallbackHandler


class PipelineLoggingCallbackHandler(BaseCallbackHandler):
    def __init__(self, pipeline) -> None:
        self.pipeline = pipeline
        self.log = ""
        self.depth = 0
        self.errored = False

    def on_chain_start(self, serialized, inputs, *args, **kwargs):
        indent = "  " * self.depth
        name = serialized.get("name", serialized.get("id", ["<unknown>"])[-1])
        self.log = f"{self.log}\n{indent}{name} inputs: {inputs}"
        self.depth += 1
        return super().on_chain_start(serialized, inputs, *args, **kwargs)

    def on_chain_end(self, outputs, **kwargs):
        from apps.pipelines.models import PipelineRun

        self.depth -= 1  # Decrease depth as a chain (or sub-chain) ends
        self.log = f"{self.log} --> output: {outputs}\n"
        if self.depth == 0:
            PipelineRun.objects.create(
                pipeline=self.pipeline, status="SUCCESS" if not self.errored else "FAILURE", log=self.log
            )
            print(self.log)

    def on_chain_error(self, error, *args, **kwargs):
        print(f"{self.log} --/--> error: {error}")
        self.log = f"{self.log} --/--> error: {error}"
