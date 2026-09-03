from unittest import mock

import pytest
from django.test import override_settings

from apps.help.tracing import get_help_agent_tracer, normalize_sample_rate


class TestNormalizeSampleRate:
    def test_zero_rate_means_trace_nothing(self):
        """langfuse==4.14.1's Langfuse.__init__ treats sample_rate=0.0 as falsy and
        silently substitutes the LANGFUSE_SAMPLE_RATE env var or 1.0, so "trace nothing"
        has to be enforced here rather than trusted to the SDK."""
        assert normalize_sample_rate(0.0) is None

    def test_blank_rate_defaults_to_one(self):
        assert normalize_sample_rate(None) == 1.0

    @pytest.mark.parametrize("rate", [0.5, 1.0], ids=["partial", "full"])
    def test_explicit_nonzero_rate_passes_through_unchanged(self, rate):
        assert normalize_sample_rate(rate) == rate


class TestGetHelpAgentTracer:
    @override_settings(LANGFUSE_PUBLIC_KEY="", LANGFUSE_SECRET_KEY="")
    def test_returns_none_when_unconfigured(self):
        assert get_help_agent_tracer() is None

    @override_settings(LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="", LANGFUSE_HOST="https://x")
    def test_returns_none_when_only_public_key_set(self):
        assert get_help_agent_tracer() is None

    @override_settings(LANGFUSE_PUBLIC_KEY="", LANGFUSE_SECRET_KEY="sk", LANGFUSE_HOST="https://x")
    def test_returns_none_when_only_secret_key_set(self):
        assert get_help_agent_tracer() is None

    @override_settings(
        LANGFUSE_PUBLIC_KEY="pk",
        LANGFUSE_SECRET_KEY="sk",
        LANGFUSE_HOST="https://x",
        LANGFUSE_SAMPLE_RATE=0.0,
    )
    def test_returns_none_when_sample_rate_is_zero(self):
        assert get_help_agent_tracer() is None

    @override_settings(
        LANGFUSE_PUBLIC_KEY="pk",
        LANGFUSE_SECRET_KEY="sk",
        LANGFUSE_HOST="https://x",
        LANGFUSE_SAMPLE_RATE=None,
    )
    def test_builds_tracer_with_configured_credentials(self):
        with mock.patch("apps.help.tracing.LangFuseTracer") as mock_tracer_cls:
            tracer = get_help_agent_tracer()

        mock_tracer_cls.assert_called_once_with(
            "langfuse",
            {"public_key": "pk", "secret_key": "sk", "host": "https://x", "sample_rate": 1.0},
        )
        assert tracer is mock_tracer_cls.return_value
