import pytest

from apps.service_providers.forms import LangfuseTraceProviderForm
from apps.service_providers.tracing.langfuse import LangFuseTracer
from apps.utils.factories.service_provider_factories import TraceProviderFactory


@pytest.mark.django_db()
class TestLangfuseTraceProviderFormSampleRate:
    def _form(self, team, **overrides):
        data = {
            "public_key": "pk",
            "secret_key": "sk",
            "host": "https://example.com",
        }
        data.update(overrides)
        return LangfuseTraceProviderForm(team, data=data)

    def test_sample_rate_is_optional(self, team):
        form = self._form(team)

        assert form.is_valid(), form.errors
        assert form.cleaned_data["sample_rate"] is None

    @pytest.mark.parametrize("value", ["0", "0.5", "1"], ids=["zero", "mid", "one"])
    def test_sample_rate_accepts_the_full_0_to_1_range(self, team, value):
        form = self._form(team, sample_rate=value)

        assert form.is_valid(), form.errors
        assert form.cleaned_data["sample_rate"] == float(value)

    @pytest.mark.parametrize("value", ["-0.1", "1.1"], ids=["below-zero", "above-one"])
    def test_sample_rate_rejects_values_outside_0_to_1(self, team, value):
        form = self._form(team, sample_rate=value)

        assert not form.is_valid()
        assert "sample_rate" in form.errors


@pytest.mark.django_db()
class TestTraceProviderGetService:
    def test_no_override_and_no_configured_sample_rate_normalizes_to_1_0(self, team):
        """A blank rate must not fall through to `Langfuse(**config)` as `None`: the SDK's own
        constructor treats `None` as "check the LANGFUSE_SAMPLE_RATE env var, else 1.0" -- an
        operator-set env var could silently override "leave blank to send every trace".
        """
        provider = TraceProviderFactory.create(team=team)

        service = provider.get_service()

        assert isinstance(service, LangFuseTracer)
        assert service.config["sample_rate"] == 1.0

    def test_none_override_with_no_configured_sample_rate_also_normalizes_to_1_0(self, team):
        provider = TraceProviderFactory.create(team=team)

        service = provider.get_service(sample_rate=None)

        assert service.config["sample_rate"] == 1.0

    def test_configured_provider_sample_rate_flows_through_unchanged(self, team):
        provider = TraceProviderFactory.create(team=team, config={"public_key": "pk", "secret_key": "sk"})
        provider.config["sample_rate"] = 0.4
        provider.save()

        service = provider.get_service()

        assert service.config["sample_rate"] == 0.4

    def test_experiment_override_takes_precedence_over_the_provider_default(self, team):
        provider = TraceProviderFactory.create(team=team)
        provider.config["sample_rate"] = 0.4
        provider.save()

        service = provider.get_service(sample_rate=0.9)

        assert service.config["sample_rate"] == 0.9

    def test_none_override_falls_through_to_the_provider_default(self, team):
        provider = TraceProviderFactory.create(team=team)
        provider.config["sample_rate"] = 0.4
        provider.save()

        service = provider.get_service(sample_rate=None)

        assert service.config["sample_rate"] == 0.4

    def test_zero_override_returns_none_instead_of_a_tracer(self, team):
        """An effective sample_rate of exactly 0.0 must not reach the Langfuse SDK: its own
        constructor treats `sample_rate=0.0` as falsy and silently substitutes its 1.0 default
        (`sample_rate or float(os.environ.get(LANGFUSE_SAMPLE_RATE, 1.0))`), so OCS enforces
        "trace nothing" itself rather than build a tracer the SDK would ignore.
        """
        provider = TraceProviderFactory.create(team=team)

        service = provider.get_service(sample_rate=0.0)

        assert service is None

    def test_zero_provider_default_with_no_override_also_returns_none(self, team):
        provider = TraceProviderFactory.create(team=team)
        provider.config["sample_rate"] = 0.0
        provider.save()

        service = provider.get_service()

        assert service is None
