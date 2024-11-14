from langchain_core.tracers import LangChainTracer
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

    def get_callback(self, participant_id: str, session_id: str):
        raise NotImplementedError

    def update_trace(self, metadata: dict):
        pass

    def get_current_trace_info(self) -> TraceInfo | None:
        return None


class LangFuseTraceService(TraceService):
    """
    Notes on langfuse:

    The API is designed to be used with a single set of credentials whereas we need to provide
    different credentials per call. This is why we don't use the standard 'observe' decorator.
    """

    def __init__(self, type_, config: dict):
        super().__init__(type_, config)
        self._callback = None

    def get_callback(self, participant_id: str, session_id: str):
        from langfuse.callback import CallbackHandler

        if self._callback:
            raise ServiceReentryException("Service does not support reentrant use.")

        self._callback = CallbackHandler(user_id=participant_id, session_id=session_id, **self.config)
        return self._callback

    def update_trace(self, metadata: dict):
        if not metadata:
            return

        if not self._callback:
            raise ServiceNotInitializedException("Service not initialized.")

        self._callback.trace.update(metadata=metadata)

    def get_current_trace_info(self) -> TraceInfo | None:
        if not self._callback:
            raise ServiceNotInitializedException("Service not initialized.")

        if self._callback.trace:
            return TraceInfo(
                trace_id=self._callback.trace.id,
                trace_url=self._callback.trace.get_trace_url(),
            )


class LangSmithTraceService(TraceService):
    def get_callback(self, participant_id: str, session_id: str):
        from langsmith import Client

        client = Client(
            api_url=self.config["api_url"],
            api_key=self.config["api_key"],
        )

        return LangChainTracer(client=client, project_name=self.config["project"])
