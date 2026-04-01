class EarlyExitResponse(Exception):
    """Raised by any core stage to short-circuit the pipeline.

    The pipeline orchestrator catches this, stores the message on
    ctx.early_exit_response, and then runs terminal stages.
    """

    def __init__(self, response: str):
        self.response = response
        super().__init__(response)
