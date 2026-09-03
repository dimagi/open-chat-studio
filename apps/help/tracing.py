from __future__ import annotations

from django.conf import settings

from apps.service_providers.tracing.langfuse import LangFuseTracer, normalize_sample_rate


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
