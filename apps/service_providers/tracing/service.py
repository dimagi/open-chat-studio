class TraceService:
    pass


class LangFuseTraceService(TraceService):
    def __init__(self, config: dict):
        self.config = config
