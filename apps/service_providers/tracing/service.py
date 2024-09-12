class TraceService:
    def get_callback(self, participant_id: str, session_id: str):
        raise NotImplementedError


class LangFuseTraceService(TraceService):
    def __init__(self, config: dict):
        self.config = config

    def get_callback(self, participant_id: str, session_id: str):
        from langfuse.callback import CallbackHandler

        return CallbackHandler(user_id=participant_id, session_id=session_id, **self.config)


class LangSmithTraceService(TraceService):
    def __init__(self, config: dict):
        self.config = config

    def get_callback(self, participant_id: str, session_id: str):
        from langchain.callbacks.tracers import LangChainTracer
        from langsmith import Client

        client = Client(
            api_url=self.config["api_url"],
            api_key=self.config["api_key"],
        )

        return LangChainTracer(client=client, project_name=self.config["project"])
