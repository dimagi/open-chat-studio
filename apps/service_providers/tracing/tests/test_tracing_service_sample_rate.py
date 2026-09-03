from unittest import mock

import pytest

from apps.service_providers.tracing.langfuse import LangFuseTracer
from apps.service_providers.tracing.service import TracingService
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.service_provider_factories import TraceProviderFactory


@pytest.mark.django_db()
class TestCreateForExperimentSampleRate:
    """`TracingService.create_for_experiment` is where `Experiment.trace_sample_rate` meets
    `TraceProvider.get_service()` -- verifies the override is threaded through, and that a
    provider `get_service()` short-circuits to `None` (see test_trace_provider_sample_rate.py)
    doesn't add a tracer to the service.
    """

    def test_passes_the_experiment_override_to_the_trace_provider(self):
        provider = TraceProviderFactory.create()
        experiment = ExperimentFactory.create(team=provider.team, trace_provider=provider, trace_sample_rate=0.25)

        with mock.patch.object(provider.__class__, "get_service", wraps=provider.get_service) as get_service:
            TracingService.create_for_experiment(experiment)

        get_service.assert_called_once_with(sample_rate=0.25)

    def test_no_override_passes_none_through_to_the_provider(self):
        provider = TraceProviderFactory.create()
        experiment = ExperimentFactory.create(team=provider.team, trace_provider=provider)

        with mock.patch.object(provider.__class__, "get_service", wraps=provider.get_service) as get_service:
            TracingService.create_for_experiment(experiment)

        get_service.assert_called_once_with(sample_rate=None)

    def test_zero_effective_sample_rate_excludes_the_langfuse_tracer(self):
        provider = TraceProviderFactory.create()
        experiment = ExperimentFactory.create(team=provider.team, trace_provider=provider, trace_sample_rate=0.0)

        service = TracingService.create_for_experiment(experiment)

        assert not any(isinstance(tracer, LangFuseTracer) for tracer in service._tracers)

    def test_nonzero_effective_sample_rate_includes_the_langfuse_tracer(self):
        provider = TraceProviderFactory.create()
        experiment = ExperimentFactory.create(team=provider.team, trace_provider=provider, trace_sample_rate=0.5)

        service = TracingService.create_for_experiment(experiment)

        assert any(isinstance(tracer, LangFuseTracer) for tracer in service._tracers)
