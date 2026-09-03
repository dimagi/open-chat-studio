from __future__ import annotations

from django.conf import settings

from apps.service_providers.tracing.langfuse import LangFuseTracer


def normalize_sample_rate(sample_rate: float | None) -> float | None:
    """Normalize a Langfuse sample rate.

    Returns ``None`` when the rate is exactly ``0.0`` ("trace nothing"):
    langfuse==4.14.1's ``Langfuse.__init__`` treats ``sample_rate=0.0`` as falsy and
    silently substitutes the ``LANGFUSE_SAMPLE_RATE`` env var or 1.0, so "trace nothing"
    has to be enforced here rather than trusted to the SDK. A blank rate is normalized
    to ``1.0`` for the same reason: passing ``None`` through lets the SDK fall back to
    that env var if one happens to be set, silently overriding "leave blank to trace
    every call".
    """
    if sample_rate == 0.0:
        return None
    if sample_rate is None:
        return 1.0
    return sample_rate


def get_help_agent_tracer() -> LangFuseTracer | None:
    """Build the operator-configured Langfuse tracer for the system agent (apps/help/).

    Returns None when LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY aren't set, or when the
    effective sample rate is 0.0, so agents run exactly as before tracing was added.
    """
    if not (settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY):
        return None

    sample_rate = normalize_sample_rate(settings.LANGFUSE_SAMPLE_RATE)
    if sample_rate is None:
        return None

    config = {
        "public_key": settings.LANGFUSE_PUBLIC_KEY,
        "secret_key": settings.LANGFUSE_SECRET_KEY,
        "host": settings.LANGFUSE_HOST,
        "sample_rate": sample_rate,
    }
    return LangFuseTracer("langfuse", config)
