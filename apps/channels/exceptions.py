class ExperimentChannelException(Exception):
    pass


class InvalidTelegramChannel(ExperimentChannelException):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


class ChannelDisabledException(ExperimentChannelException):
    """Raised when something tries to open a conversation on a channel an admin has switched off.

    ``ChannelDisabledStage`` covers inbound messages on channels that already have a session.
    This covers everything that runs *before* a pipeline: every route into
    ``start_experiment_session``. Callers catch it (or check ``is_disabled`` first) and turn it
    into whatever refusal suits their surface -- an HTTP error, a form message, or silence.

    ``disabled_message`` is the admin's optional static text, carried here so callers can relay
    it without re-fetching the channel. It is blank when the admin chose to stay silent.
    """

    def __init__(self, channel):
        self.channel = channel
        self.disabled_message = channel.disabled_message
        super().__init__(f"Channel {channel.id} ({channel.platform}) is disabled")


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
    """
