import pytest

from apps.service_providers.tracing.langfuse import normalize_sample_rate


class TestNormalizeSampleRate:
    def test_zero_rate_means_trace_nothing(self):
        """langfuse's Langfuse.__init__ treats sample_rate=0.0 as falsy and silently
        substitutes the LANGFUSE_SAMPLE_RATE env var or 1.0, so "trace nothing" has to be
        enforced here rather than trusted to the SDK."""
        assert normalize_sample_rate(0.0) is None

    def test_blank_rate_defaults_to_one(self):
        assert normalize_sample_rate(None) == 1.0

    @pytest.mark.parametrize("rate", [0.5, 1.0], ids=["partial", "full"])
    def test_explicit_nonzero_rate_passes_through_unchanged(self, rate):
        assert normalize_sample_rate(rate) == rate
