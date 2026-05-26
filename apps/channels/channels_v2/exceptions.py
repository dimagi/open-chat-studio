class EarlyExitResponse(Exception):
    """Raised by any core stage to short-circuit the pipeline.

    The pipeline orchestrator catches this, stores the message on
    ctx.early_exit_response, and then runs terminal stages.
    """

    def __init__(self, response: str):
        self.response = response
        super().__init__(response)


class EarlyAbort(Exception):
    """Raised by a core stage to halt the pipeline silently.

    Unlike EarlyExitResponse, no user-facing message is sent and no
    terminal stages run. This is for situations where processing must
    stop but reporting anything back to the user (or attempting to)
    would be wrong -- e.g. the participant has revoked platform-level
    consent, or the channel can no longer reach them.

    ``reason`` is optional diagnostic context shown in logs/traces; it
    is never surfaced to the user.
    """

    def __init__(self, reason: str = ""):
        self.reason = reason
        super().__init__(reason)
