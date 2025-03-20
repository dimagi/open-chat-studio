from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel


class ServiceReentryException(Exception):
    pass


class ServiceNotInitializedException(Exception):
    pass


class TraceInfo(BaseModel):
    trace_id: str
    trace_url: str


class TraceService:
    def __init__(self, type_, config: dict):
        self.type = type_
        self.config = config

    def get_callback(self, participant_id: str, session_id: str) -> BaseCallbackHandler:
        raise NotImplementedError

    def get_trace_metadata(self) -> TraceInfo | None:
        return None
