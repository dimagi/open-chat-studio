from langchain_core.tracers import LangChainTracer
from pydantic import BaseModel


class TraceInfo(BaseModel):
    trace_id: str
    trace_url: str


class TraceService:
    def get_callback(self, participant_id: str, session_id: str):
        raise NotImplementedError

    def update_trace(self, metadata: dict):
        pass

    def get_current_trace_info(self) -> TraceInfo | None:
        return None


class LangFuseTraceService(TraceService):
    def __init__(self, config: dict):
        self.config = config

    def get_callback(self, participant_id: str, session_id: str):
        from langfuse.callback import CallbackHandler

        return CallbackHandler(user_id=participant_id, session_id=session_id, **self.config)

    def update_trace(self, metadata: dict):
        from langfuse.decorators import langfuse_context

        langfuse_context.update_current_trace(metadata={"key": "value"})

    def get_current_trace_info(self) -> TraceInfo:
        from langfuse.decorators import langfuse_context

        return TraceInfo(
            trace_id=langfuse_context.get_current_trace_id(),
            trace_url=langfuse_context.get_current_trace_url(),
        )


class LangSmithTraceService(TraceService):
    def __init__(self, config: dict):
        self.config = config

    def get_callback(self, participant_id: str, session_id: str):
        from langsmith import Client

        client = Client(
            api_url=self.config["api_url"],
            api_key=self.config["api_key"],
        )

        return LangChainTracer(client=client, project_name=self.config["project"])
