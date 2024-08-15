class TraceService:
    def get_callback(self, participant_id: str, session_id: str):
        raise NotImplementedError


class LangFuseTraceService(TraceService):
    def __init__(self, config: dict):
        self.config = config

    def get_callback(self, participant_id: str, session_id: str):
        from langfuse.callback import CallbackHandler

        return CallbackHandler(user_id=participant_id, session_id=session_id, **self.config)
