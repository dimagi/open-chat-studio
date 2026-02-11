from functools import wraps


def silence_exceptions(logger=None, log_message: str | None = None):
    def decorate(f):
        """Decorator to make a function safe by catching exceptions and logging them."""

        @wraps(f)
        def safe_func(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except Exception:
                if logger and log_message:
                    logger.exception(log_message)

        return safe_func

    return decorate
