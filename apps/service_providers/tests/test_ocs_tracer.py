from uuid import uuid4

import pytest

from apps.service_providers.tracing.ocs_tracer import OCSTracer
from apps.trace.models import Trace
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
class TestOCSTracer:
    def test_ending_trace_creates_trace_object(self, experiment):
        tracer = OCSTracer(experiment.id, experiment.team_id)
        session = ExperimentSessionFactory()

        tracer.end_trace()
        # The trace was never started, so no Trace object should be created
        assert Trace.objects.count() == 0

        tracer.start_trace(
            trace_name="test_trace",
            trace_id=uuid4(),
            session=session,
        )

        tracer.end_trace()
        assert Trace.objects.count() == 1
