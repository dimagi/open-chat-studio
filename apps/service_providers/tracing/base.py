from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig


class ServiceReentryException(Exception):
    pass


class ServiceNotInitializedException(Exception):
    pass


class TraceService:
    def __init__(self, type_, config: dict):
        self.type = type_
        self.config = config

    def get_callback(self, trace_name: str, participant_id: str, session_id: str) -> BaseCallbackHandler:
        raise NotImplementedError

    def get_langchain_config(self, trace_name: str, participant_id: str, session_id: str) -> RunnableConfig:
        callback = self.get_callback(
            trace_name=trace_name,
            participant_id=participant_id,
            session_id=session_id,
        )
        return {
            "run_name": trace_name,
            "callbacks": [callback],
            "metadata": {
                "participant-id": participant_id,
                "session-id": session_id,
            },
        }

    def get_trace_metadata(self) -> dict[str, str]:
        return {}

    def end(self):
        pass
