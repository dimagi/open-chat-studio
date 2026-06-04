from datetime import datetime
from functools import wraps

from django.utils.http import http_date
from django.utils.timezone import is_naive


def sunset(sunset_at: datetime, successor_url: str | None = None):
    """Mark a view as deprecated with RFC 8594 `Sunset` / `Deprecation` response headers.

    Use during a deprecation window for public endpoints. See
    docs/developer_guides/feature_deprecation.md. On public views, `@waf_allow`
    must remain the first decorator.
    """
    if is_naive(sunset_at):
        raise ValueError("sunset_at must be timezone-aware")

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            response = view_func(request, *args, **kwargs)
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = http_date(sunset_at.timestamp())
            if successor_url:
                response.headers["Link"] = f'<{successor_url}>; rel="successor-version"'
            return response

        return wrapper

    return decorator


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
